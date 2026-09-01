"""Bounded deterministic GitLab repository read operations."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping
from urllib.parse import quote, unquote, urlsplit

import yaml
import httpx

if __package__:
    from .client import GitLabClient
    from ._common.envelope import UNTRUSTED_CONTENT_WARNING
    from ._common.errors import ConnectorError
    from ._common.guardrails import require_explicit_intent
    from .models import GitLabError, PageResult
else:  # Standalone source tests import modules directly from the plugin root.
    from client import GitLabClient
    from _common.envelope import UNTRUSTED_CONTENT_WARNING
    from _common.errors import ConnectorError
    from _common.guardrails import require_explicit_intent
    from models import GitLabError, PageResult


_MAX_PROJECT_REFERENCE = 2048
_MAX_PROJECT_SLUG = 1024
_MAX_GROUP_REFERENCE = 2048
_MAX_GROUP_SLUG = 1024
_MAX_GROUPS = 2000
_MAX_GROUP_PROJECTS = 5000
_MAX_COMMITS = 2000
_MAX_BRANCHES = 2000
_MAX_TAGS = 2000
_MAX_RELEASES = 1000
_MAX_TODOS = 2000
_MAX_RELEASE_DESCRIPTION = 128 * 1024
_MAX_RELEASE_SUMMARY_BYTES = 2048
_MAX_RELEASE_MILESTONES = 100
_MAX_RELEASE_ASSETS = 500
_MAX_SEARCH_QUERY = 1024
_MAX_SEARCH_RESULTS = 2000
_MAX_PROJECT_DESCRIPTION = 2048
_MAX_SEARCH_SNIPPET_BYTES = 4096
_MAX_SEARCH_TOTAL_BYTES = 128 * 1024
_MAX_SEARCH_RESULTS_PER_CALL = _MAX_SEARCH_TOTAL_BYTES // _MAX_SEARCH_SNIPPET_BYTES
_MAX_JOBS = 2000
_MAX_COMMIT_TEXT = 128 * 1024
_MAX_JOB_TEXT = 2048
_MAX_COMMIT_STAT = 1_000_000_000
_MAX_COMMENTS = 2000
_MAX_DISCUSSIONS = 1000
_MAX_NOTES_PER_DISCUSSION = 500
_MAX_NOTE_BODY = 128 * 1024
_MAX_MERGE_REQUESTS = 2000
_MAX_MR_LABELS = 1000
_MAX_REF = 512
_MAX_PATH = 4096
_MAX_TREE_ITEMS = 2000
_MAX_PIPELINES = 500
_MAX_FILE_BYTES = 512 * 1024
_MAX_CI_BRANCHES = 100
_MAX_CI_PAGES = 10
_MAX_CI_INCLUDES = 100
_MAX_CI_INCLUDE_BYTES = 512 * 1024
_MAX_CI_GROUPS = 20
_MAX_CI_VARIABLES = 2000
_MAX_CI_YAML_NODES = 4096
_MAX_CI_YAML_DEPTH = 64
_MAX_CI_YAML_ALIASES = 128
_MAX_LOG_BYTES = 200_000
_MAX_WRITE_ACTIONS = 100
_MAX_WRITE_BYTES = 512 * 1024
_MAX_COMMIT_MESSAGE = 4096
_MAX_TAG_MESSAGE = 8192
_MAX_MR_TITLE = 255
_MAX_MR_TITLE_INPUT = 1024
_MAX_MR_DESCRIPTION = 64 * 1024
_MAX_NOTE_BYTES = 100_000
_DUPLICATE_MR_MESSAGE = "another open merge request already exists"
_DISCUSSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_MR_DRAFT_PREFIX = re.compile(r"^(?:draft:|\[draft\]|\(draft\)|wip:)", re.I)
_GITLAB_USERNAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}$")
_MR_STATE_EVENTS = frozenset({"close", "reopen"})
_MR_RESPONSE_STATES = frozenset({"opened", "closed", "merged", "locked"})
_MR_SCOPES = frozenset({"all", "created_by_me", "assigned_to_me", "reviews_for_me"})
_MR_SCOPE_ACTORS = {
    "created_by_me": "author",
    "assigned_to_me": "assignee",
    "reviews_for_me": "reviewer",
}
_TODO_STATES = frozenset({"pending", "done"})
_TODO_ACTIONS = frozenset({
    "assigned", "mentioned", "build_failed", "marked", "approval_required",
    "unmergeable", "directly_addressed", "merge_train_removed",
    "member_access_requested",
})
_TODO_TARGET_TYPES = frozenset({
    "Issue", "MergeRequest", "Commit", "Epic", "DesignManagement::Design",
    "AlertManagement::Alert", "Project", "Namespace", "Vulnerability",
    "WikiPage::Meta",
})
_TODO_IID_TARGET_TYPES = frozenset({
    "Issue", "MergeRequest", "Epic", "DesignManagement::Design",
})
_CI_STARTED_STATUSES = frozenset(
    {
        "created",
        "waiting_for_resource",
        "preparing",
        "waiting_for_callback",
        "pending",
        "running",
        "scheduled",
    }
)


class _YamlCapacityError(Exception):
    """Raised before a CI YAML document can exhaust local parser capacity."""


class _BoundedComposeLoader(yaml.SafeLoader):
    """Compose YAML nodes without constructing tagged application objects."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._node_count = 0
        self._depth = 0
        self._alias_count = 0

    def compose_node(self, parent: Any, index: Any) -> yaml.nodes.Node:
        self._node_count += 1
        if self._node_count > _MAX_CI_YAML_NODES:
            raise _YamlCapacityError
        if self.check_event(yaml.events.AliasEvent):
            self._alias_count += 1
            if self._alias_count > _MAX_CI_YAML_ALIASES:
                raise _YamlCapacityError
        self._depth += 1
        if self._depth > _MAX_CI_YAML_DEPTH:
            raise _YamlCapacityError
        try:
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1


def _bounded_string(value: Any, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise GitLabError("invalid_input")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise GitLabError("invalid_input") from None
    value = value.strip()
    if (not value and not allow_empty) or len(value) > maximum or "\x00" in value:
        raise GitLabError("invalid_input")
    return value


def _reject_hidden_quick_actions(value: str) -> str:
    """Reject GitLab quick actions on every line of user-authored text."""
    if any(line.lstrip().startswith("/") for line in value.splitlines()):
        raise GitLabError("invalid_input")
    return value


def _validate_path(path: str, *, allow_empty: bool = True) -> str:
    path = _bounded_string(path, _MAX_PATH, allow_empty=allow_empty)
    if path.startswith("/") or any(part in {".", ".."} for part in path.split("/")):
        raise GitLabError("invalid_input")
    return path


def _git_ref_is_valid(ref: Any) -> bool:
    if not isinstance(ref, str) or not ref or len(ref) > _MAX_REF:
        return False
    try:
        ref.encode("utf-8")
    except UnicodeEncodeError:
        return False
    if ref == "@" or ref.startswith(("/", "-")) or ref.endswith(("/", ".")):
        return False
    if "//" in ref or ".." in ref or "@{" in ref:
        return False
    if any(
        character.isspace()
        or ord(character) < 32
        or ord(character) == 127
        or character in "~^:?*[\\"
        for character in ref
    ):
        return False
    parts = ref.split("/")
    return all(
        part and not part.startswith(".") and not part.endswith(".lock")
        for part in parts
    )


def _validate_ref(ref: str) -> str:
    if not _git_ref_is_valid(ref):
        raise GitLabError("invalid_input")
    return ref


def _validate_remote_ref(ref: Any) -> str:
    if not _git_ref_is_valid(ref):
        raise GitLabError("invalid_remote_data")
    return ref


def _positive_bound(value: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise GitLabError("invalid_input")
    return value


def _remote_positive_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GitLabError("invalid_remote_data")
    return value


def _as_object(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GitLabError("invalid_remote_data")
    return value


def _as_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise GitLabError("invalid_remote_data")
    return value


def _remote_text(
    source: Mapping[str, Any],
    field: str,
    maximum: int,
    *,
    optional: bool = False,
) -> str | None:
    value = source.get(field)
    if optional and value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or "\x00" in value
    ):
        raise GitLabError("invalid_remote_data")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise GitLabError("invalid_remote_data") from None
    return value


def _bounded_enum(value: Any, allowed: frozenset[str], maximum: int) -> str:
    if not isinstance(value, str) or value != value.strip() or "\x00" in value:
        raise GitLabError("invalid_input")
    try:
        if len(value.encode("utf-8")) > maximum:
            raise GitLabError("invalid_input")
    except UnicodeEncodeError:
        raise GitLabError("invalid_input") from None
    if value not in allowed:
        raise GitLabError("invalid_input")
    return value


def _remote_open_text(value: Any, maximum: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        raise GitLabError("invalid_remote_data")
    try:
        if len(value.encode("utf-8")) > maximum:
            raise GitLabError("invalid_remote_data")
    except UnicodeEncodeError:
        raise GitLabError("invalid_remote_data") from None
    return value


def _gitlab_username(value: Any, *, remote: bool) -> str:
    category = "invalid_remote_data" if remote else "invalid_input"
    if not isinstance(value, str) or value != value.strip() or "\x00" in value:
        raise GitLabError(category)
    try:
        if len(value.encode("utf-8")) > 255:
            raise GitLabError(category)
    except UnicodeEncodeError:
        raise GitLabError(category) from None
    if _GITLAB_USERNAME.fullmatch(value) is None:
        raise GitLabError(category)
    return value


def _same_origin_url(value: Any, origin: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
    ):
        raise GitLabError("invalid_remote_data")
    try:
        if len(value.encode("utf-8")) > _MAX_PROJECT_REFERENCE:
            raise GitLabError("invalid_remote_data")
    except UnicodeEncodeError:
        raise GitLabError("invalid_remote_data") from None
    try:
        parsed = urlsplit(value)
        configured = urlsplit(origin)
        parsed_port = parsed.port
        configured_port = configured.port
    except ValueError:
        raise GitLabError("invalid_remote_data") from None
    if (
        parsed.scheme != configured.scheme
        or parsed.hostname != configured.hostname
        or parsed_port != configured_port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise GitLabError("invalid_remote_data")
    return value


def _canonical_remote_url(value: Any, origin: str, expected_path: str) -> str:
    _same_origin_url(value, origin)
    parsed = urlsplit(value)
    if unquote(parsed.path) != expected_path:
        raise GitLabError("invalid_remote_data")
    return f"{origin}{quote(expected_path, safe='/')}"


def _project_endpoint(project: str | int) -> str:
    if isinstance(project, bool):
        raise GitLabError("invalid_input")
    if isinstance(project, int):
        if project <= 0:
            raise GitLabError("invalid_input")
        return str(project)
    value = _bounded_string(project, _MAX_PROJECT_SLUG)
    if value.isdigit():
        numeric_project = int(value)
        if numeric_project <= 0:
            raise GitLabError("invalid_input")
        return str(numeric_project)
    if "/" not in value or value.startswith("/") or value.endswith("/"):
        raise GitLabError("group_ambiguity")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise GitLabError("invalid_input")
    return quote(value, safe="")


def _namespace_path(value: Any, *, remote: bool) -> str:
    category = "invalid_remote_data" if remote else "invalid_input"
    maximum = _MAX_GROUP_SLUG if remote else _MAX_GROUP_REFERENCE
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise GitLabError(category)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise GitLabError(category) from None
    if value != value.strip() or value.startswith("/") or value.endswith("/"):
        raise GitLabError(category)
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise GitLabError(category)
    if "\x00" in value:
        raise GitLabError(category)
    return value


def _group_endpoint(group: str | int) -> str:
    if isinstance(group, bool):
        raise GitLabError("invalid_input")
    if isinstance(group, int):
        if group <= 0:
            raise GitLabError("invalid_input")
        return str(group)
    value = _bounded_string(group, _MAX_GROUP_SLUG)
    if value.isdigit():
        numeric_group = int(value)
        if numeric_group <= 0:
            raise GitLabError("invalid_input")
        return str(numeric_group)
    return quote(_namespace_path(value, remote=False), safe="")


def _continuation_source(value: Any) -> tuple[int, int]:
    if value is None:
        return 1, 0
    if not isinstance(value, Mapping) or not set(value).issubset(
        {"page", "next_page", "offset"}
    ):
        raise GitLabError("invalid_input")
    if "page" in value and "next_page" in value:
        raise GitLabError("invalid_input")
    page = value.get("page", value.get("next_page", 1))
    offset = value.get("offset", 0)
    if (
        isinstance(page, bool)
        or not isinstance(page, int)
        or page <= 0
        or isinstance(offset, bool)
        or not isinstance(offset, int)
        or offset < 0
        or offset >= 100
    ):
        raise GitLabError("invalid_input")
    return page, offset


def _rfc3339(value: Any, *, remote: bool) -> tuple[datetime, str]:
    category = "invalid_remote_data" if remote else "invalid_input"
    if not isinstance(value, str) or not value or "\x00" in value:
        raise GitLabError(category)
    try:
        if len(value.encode("utf-8")) > 128:
            raise GitLabError(category)
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise GitLabError(category) from None
    except UnicodeEncodeError:
        raise GitLabError(category) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GitLabError(category)
    utc = parsed.astimezone(timezone.utc)
    return utc, utc.isoformat().replace("+00:00", "Z")


def _commit_sha(value: Any, *, short: bool = False, remote: bool = True) -> str:
    category = "invalid_remote_data" if remote else "invalid_input"
    minimum = 7 if short else 40
    maximum = 40
    if (
        not isinstance(value, str)
        or not minimum <= len(value) <= maximum
        or re.fullmatch(r"[0-9a-fA-F]+", value) is None
    ):
        raise GitLabError(category)
    return value.lower()


def _remote_repo_path(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_PATH:
        raise GitLabError("invalid_remote_data")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise GitLabError("invalid_remote_data") from None
    if value != value.strip() or value.startswith("/") or any(
        part in {"", ".", ".."} for part in value.split("/")
    ) or "\x00" in value:
        raise GitLabError("invalid_remote_data")
    return value


def _build_branch_name(prefix: Any, ticket_key: Any, summary: Any) -> str:
    prefix = _bounded_string(prefix, _MAX_REF).rstrip("/")
    ticket_key = _bounded_string(ticket_key, 128)
    summary = _bounded_string(summary, 2048)
    if not prefix or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", ticket_key):
        raise GitLabError("invalid_input")
    slug = re.sub(r"[^a-z0-9]+", "-", summary.lower()).strip("-")
    slug = slug[:30].rstrip("-")
    if not slug:
        raise GitLabError("invalid_input")
    return _validate_ref(f"{prefix}/{ticket_key}-{slug}")


class GitLabOperations:
    def __init__(
        self,
        client: GitLabClient,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.client = client
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _redact_text(self, value: str) -> str:
        """Strip the configured PAT out of remote text before returning it."""
        secret = getattr(self.client.auth, "pat", "")
        if isinstance(secret, str) and len(secret) >= 4:
            value = value.replace(secret, "<redacted>")
        return value

    def _remote_display_text(
        self, source: Mapping[str, Any], field: str, maximum: int
    ) -> str:
        value = self._redact_text(_remote_text(source, field, maximum))
        return value.encode("utf-8")[:maximum].decode("utf-8", errors="ignore")

    @staticmethod
    def _iid(value: Any) -> int:
        if type(value) is not int or value < 1:
            raise GitLabError("invalid_input")
        return value

    @staticmethod
    def _note_body(value: Any) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value.encode("utf-8")) > _MAX_NOTE_BYTES
        ):
            raise GitLabError("invalid_input")
        return _reject_hidden_quick_actions(value)

    @staticmethod
    def _discussion_id(value: Any) -> str:
        if not isinstance(value, str) or _DISCUSSION_ID.fullmatch(value) is None:
            raise GitLabError("invalid_input")
        return value

    @staticmethod
    def _sha(value: Any) -> str:
        if not isinstance(value, str) or _SHA.fullmatch(value) is None:
            raise GitLabError("invalid_input")
        return value

    def job_log(
        self, project: str | int, job_id: int, *, max_bytes: int = 20_000
    ) -> dict[str, Any]:
        """Fetch one job's trace, biased to the tail.

        A failing job's cause is at the end of its log, so truncation keeps
        the tail and discards the head -- the opposite of the usual choice,
        and the reason this does not reuse the generic list envelope.
        """
        if type(job_id) is not int or job_id < 1:
            raise GitLabError("invalid_input")
        if type(max_bytes) is not int or not 1 <= max_bytes <= _MAX_LOG_BYTES:
            raise GitLabError("invalid_input")
        resolved = self.resolve_project(project)
        response = self.client.request_raw(
            "GET", f"/api/v4/projects/{resolved['id']}/jobs/{job_id}/trace"
        )
        raw = response.body
        total = len(raw)
        raw_exceeds_max_bytes = total > max_bytes
        # Redact the complete transport-bounded response before choosing the
        # presentation tail. Otherwise a tail boundary inside the PAT turns
        # the secret into an unrecognisable fragment that evades replacement.
        redacted = self._redact_text(raw.decode("utf-8", errors="replace"))
        encoded = redacted.encode("utf-8")
        presentation_truncated = len(encoded) > max_bytes
        if presentation_truncated:
            text = encoded[-max_bytes:].decode("utf-8", errors="ignore")
        else:
            text = redacted
        truncated = presentation_truncated
        result: dict[str, Any] = {
            "job_id": job_id,
            "log": text,
            "truncated": truncated,
            "total_bytes": total,
            "returned_bytes": len(text.encode("utf-8")),
            "content_warning": UNTRUSTED_CONTENT_WARNING,
        }
        if truncated:
            if raw_exceeds_max_bytes:
                result["hint"] = (
                    "Only the last portion of the log is shown, because a failing "
                    "job's cause is normally at the end. Raise max_bytes to see "
                    "more."
                )
            else:
                result["hint"] = (
                    "The displayed log was shortened after decoding or redaction "
                    "to remain within max_bytes."
                )
        return result

    def _ci_action(
        self,
        project: str | int,
        identifier: int,
        endpoint: str,
        label: str,
        *,
        dry_run: bool,
        confirm: bool,
    ) -> tuple[dict[str, Any], int, int | None, Any]:
        """Validate, gate, and dispatch one non-retried CI mutation."""
        if type(identifier) is not int or identifier < 1:
            raise GitLabError("invalid_input")
        try:
            execute = require_explicit_intent(
                dry_run=dry_run,
                confirm=confirm,
                action=f"{label} {identifier}",
            )
        except ConnectorError as exc:
            raise GitLabError(exc.category) from None

        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)
        project_id = resolved.get("id")
        project_path = resolved.get("path", resolved.get("path_with_namespace"))
        if (
            type(project_id) is not int
            or project_id < 1
            or not isinstance(project_path, str)
        ):
            raise GitLabError("invalid_remote_data")
        base = {
            "ok": True,
            "dry_run": not execute,
            "project": project_path,
        }
        if not execute:
            return base, project_id, None, None

        status, payload = self._write_json(
            "POST",
            f"/api/v4/projects/{project_id}/{endpoint}",
            {},
            deadline=deadline,
        )
        if status >= 400:
            raise self.client._error_for_status(status)
        return base, project_id, status, payload

    @staticmethod
    def _ci_started_status(payload: Mapping[str, Any]) -> str:
        """Require exact bounded evidence that a CI action started work."""
        status = payload.get("status")
        if (
            type(status) is not str
            or status not in _CI_STARTED_STATUSES
            or status != status.strip()
            or len(status) > 64
            or "\x00" in status
        ):
            raise GitLabError("invalid_remote_data")
        return status

    def retry_job(
        self,
        project: str | int,
        job_id: int,
        *,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Retry one CI job, returning GitLab's distinct new job identity."""
        base, _project_id, status, payload = self._ci_action(
            project,
            job_id,
            f"jobs/{job_id}/retry",
            "CI job",
            dry_run=dry_run,
            confirm=confirm,
        )
        base["job_id"] = job_id
        if status is None:
            return base

        def finish_retry_job():
            if status != 201 or not isinstance(payload, Mapping):
                raise GitLabError("invalid_remote_data")
            new_job_id = payload.get("id")
            if (
                type(new_job_id) is not int
                or new_job_id < 1
                or new_job_id == job_id
            ):
                raise GitLabError("invalid_remote_data")
            return {
                **base,
                "new_job_id": new_job_id,
                "status": self._ci_started_status(payload),
            }

        return self._usable_write_result(finish_retry_job)

    def play_job(
        self,
        project: str | int,
        job_id: int,
        *,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Start one manual CI job without changing its identity."""
        base, _project_id, status, payload = self._ci_action(
            project,
            job_id,
            f"jobs/{job_id}/play",
            "manual CI job",
            dry_run=dry_run,
            confirm=confirm,
        )
        base["job_id"] = job_id
        if status is None:
            return base

        def finish_play_job():
            if status != 200 or not isinstance(payload, Mapping):
                raise GitLabError("invalid_remote_data")
            if type(payload.get("id")) is not int or payload["id"] != job_id:
                raise GitLabError("invalid_remote_data")
            return {**base, "status": self._ci_started_status(payload)}

        return self._usable_write_result(finish_play_job)

    def retry_pipeline(
        self,
        project: str | int,
        pipeline_id: int,
        *,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Retry failed or canceled jobs in one existing CI pipeline."""
        base, project_id, status, payload = self._ci_action(
            project,
            pipeline_id,
            f"pipelines/{pipeline_id}/retry",
            "pipeline",
            dry_run=dry_run,
            confirm=confirm,
        )
        base["pipeline_id"] = pipeline_id
        if status is None:
            return base

        def finish_retry_pipeline():
            if status != 201 or not isinstance(payload, Mapping):
                raise GitLabError("invalid_remote_data")
            if (
                type(payload.get("id")) is not int
                or payload["id"] != pipeline_id
                or type(payload.get("project_id")) is not int
                or payload["project_id"] != project_id
            ):
                raise GitLabError("invalid_remote_data")
            return {**base, "status": self._ci_started_status(payload)}

        return self._usable_write_result(finish_retry_pipeline)

    def _parse_project_reference(self, reference: str | int) -> dict[str, Any]:
        if isinstance(reference, int) and not isinstance(reference, bool):
            return {"project": str(reference), "link_kind": "root", "link_suffix": ""}
        value = _bounded_string(reference, _MAX_PROJECT_REFERENCE)
        if value.isdigit():
            return {"project": str(int(value)), "link_kind": "root", "link_suffix": ""}
        if not value.startswith(("http://", "https://")):
            return {
                "project": value.removesuffix(".git"),
                "link_kind": "root",
                "link_suffix": "",
            }

        parsed = urlsplit(value)
        configured = urlsplit(self.client.auth.origin)
        if (
            parsed.scheme != configured.scheme
            or parsed.hostname != configured.hostname
            or parsed.port != configured.port
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise GitLabError("invalid_input")
        path = unquote(parsed.path).strip("/")
        if not path:
            raise GitLabError("group_ambiguity")
        if "/-/" not in path:
            slug = path.removesuffix(".git")
            return {"project": slug, "link_kind": "root", "link_suffix": ""}
        slug, suffix = path.split("/-/", 1)
        slug = slug.removesuffix(".git")
        kind, separator, remainder = suffix.partition("/")
        if kind not in {"tree", "blob"} or not separator or not remainder:
            raise GitLabError("invalid_input")
        return {"project": slug, "link_kind": kind, "link_suffix": remainder}

    def _parse_group_reference(self, reference: str | int) -> str:
        if isinstance(reference, int) and not isinstance(reference, bool):
            if reference <= 0:
                raise GitLabError("invalid_input")
            return str(reference)
        value = _bounded_string(reference, _MAX_GROUP_REFERENCE)
        if value.isdigit():
            numeric_group = int(value)
            if numeric_group <= 0:
                raise GitLabError("invalid_input")
            return str(numeric_group)
        if not value.startswith(("http://", "https://")):
            return _namespace_path(value, remote=False)

        parsed = urlsplit(value)
        configured = urlsplit(self.client.auth.origin)
        if (
            parsed.scheme != configured.scheme
            or parsed.hostname != configured.hostname
            or parsed.port != configured.port
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise GitLabError("invalid_input")
        path = unquote(parsed.path).strip("/")
        if not path or "/-/" in path or path.endswith(".git"):
            raise GitLabError("invalid_input")
        if path.startswith("groups/"):
            path = path.removeprefix("groups/")
        return _namespace_path(path, remote=False)

    def resolve_project(
        self,
        reference: str | int,
        *,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        if deadline is None:
            deadline = self.client.operation_deadline()
        parsed = self._parse_project_reference(reference)
        endpoint = _project_endpoint(parsed["project"])
        payload = _as_object(
            self.client.get_json(f"/api/v4/projects/{endpoint}", deadline=deadline)
        )
        project_id = payload.get("id")
        slug = _namespace_path(payload.get("path_with_namespace"), remote=True)
        name = _remote_text(payload, "name", 512)
        web_url = payload.get("web_url")
        if (
            isinstance(project_id, bool)
            or not isinstance(project_id, int)
            or project_id <= 0
            or "/" not in slug
        ):
            raise GitLabError("invalid_remote_data")
        _same_origin_url(web_url, self.client.auth.origin)
        if unquote(urlsplit(web_url).path).strip("/") != slug:
            raise GitLabError("invalid_remote_data")
        default = payload.get("default_branch")
        fallback = default is None or default == ""
        if not fallback and not isinstance(default, str):
            raise GitLabError("invalid_remote_data")
        default_branch = "main" if fallback else _validate_remote_ref(default)
        result: dict[str, Any] = {
            "id": project_id,
            "name": name,
            "path_with_namespace": slug,
            "default_branch": default_branch,
            "default_branch_fallback": fallback,
            "web_url": f"{self.client.auth.origin}/{quote(slug, safe='/')}",
            "origin": self.client.auth.origin,
            "link_kind": parsed["link_kind"],
            "link_suffix": parsed["link_suffix"],
        }
        if parsed["link_kind"] in {"tree", "blob"}:
            resolved_ref, repository_path = self._resolve_link_ref(
                project_id, parsed["link_suffix"], deadline=deadline
            )
            result["resolved_ref"] = resolved_ref
            result["repository_path"] = repository_path
        else:
            result["resolved_ref"] = default_branch
            result["repository_path"] = ""
        root_link = result["web_url"]
        selected_ref = quote(result["resolved_ref"], safe="")
        encoded_path = quote(result["repository_path"], safe="/")
        suffix = f"/{encoded_path}" if encoded_path else ""
        result["links"] = {
            "root": root_link,
            "tree": f"{root_link}/-/tree/{selected_ref}{suffix}",
            "blob": (
                f"{root_link}/-/blob/{selected_ref}{suffix}"
                if parsed["link_kind"] == "blob"
                else None
            ),
        }
        return result

    def _normalize_group(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        group_id = _remote_positive_int(payload.get("id"))
        name = _remote_text(payload, "name", 512)
        full_path = _namespace_path(payload.get("full_path"), remote=True)
        parent_id = payload.get("parent_id")
        if (
            name != name.strip()
            or (parent_id is not None and (
                isinstance(parent_id, bool)
                or not isinstance(parent_id, int)
                or parent_id <= 0
            ))
        ):
            raise GitLabError("invalid_remote_data")
        web_url = _canonical_remote_url(
            payload.get("web_url"),
            self.client.auth.origin,
            f"/groups/{full_path}",
        )
        return {
            "id": group_id,
            "name": name,
            "full_path": full_path,
            "parent_id": parent_id,
            "web_url": web_url,
        }

    def _normalize_group_project(
        self,
        payload: Mapping[str, Any],
        *,
        root_path: str,
    ) -> dict[str, Any]:
        project_id = _remote_positive_int(payload.get("id"))
        name = _remote_text(payload, "name", 512)
        path = _namespace_path(payload.get("path_with_namespace"), remote=True)
        if "/" not in path:
            raise GitLabError("invalid_remote_data")
        namespace = _as_object(payload.get("namespace"))
        namespace_kind = namespace.get("kind")
        namespace_path = _namespace_path(namespace.get("full_path"), remote=True)
        if namespace_kind not in {"group", "user"} or path.rsplit("/", 1)[0] != namespace_path:
            raise GitLabError("invalid_remote_data")
        archived = payload.get("archived")
        if not isinstance(archived, bool):
            raise GitLabError("invalid_remote_data")
        default_branch = payload.get("default_branch")
        if default_branch in {None, ""}:
            default_branch = None
        else:
            default_branch = _validate_remote_ref(default_branch)
        web_url = _canonical_remote_url(
            payload.get("web_url"),
            self.client.auth.origin,
            f"/{path}",
        )
        shared = not (
            namespace_path == root_path or namespace_path.startswith(root_path + "/")
        )
        return {
            "id": project_id,
            "name": name,
            "path_with_namespace": path,
            "owning_namespace": namespace_path,
            "namespace_kind": namespace_kind,
            "default_branch": default_branch,
            "archived": archived,
            "shared": shared,
            "web_url": web_url,
        }

    def list_group_projects(
        self,
        group: str | int,
        *,
        recursive: bool = True,
        include_shared: bool = False,
        include_archived: bool = False,
        search: str | None = None,
        max_groups: int = 200,
        max_projects: int = 500,
        continuation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not all(
            isinstance(value, bool)
            for value in (recursive, include_shared, include_archived)
        ):
            raise GitLabError("invalid_input")
        max_groups = _positive_bound(max_groups, _MAX_GROUPS)
        max_projects = _positive_bound(max_projects, _MAX_GROUP_PROJECTS)
        if search is not None:
            search = _bounded_string(search, 512)
        if continuation is None:
            continuation = {}
        if not isinstance(continuation, Mapping) or not set(continuation).issubset(
            {"groups", "projects"}
        ):
            raise GitLabError("invalid_input")
        group_page, group_offset = _continuation_source(continuation.get("groups"))
        project_page, project_offset = _continuation_source(
            continuation.get("projects")
        )

        deadline = self.client.operation_deadline()
        reference = self._parse_group_reference(group)
        endpoint = _group_endpoint(reference)
        root = self._normalize_group(
            _as_object(
                self.client.get_json(
                    f"/api/v4/groups/{endpoint}",
                    deadline=deadline,
                )
            )
        )
        groups: list[dict[str, Any]] = [root]
        if recursive and max_groups > 1:
            group_pages = self._paginate(
                f"/api/v4/groups/{root['id']}/descendant_groups",
                params={"order_by": "path", "sort": "asc"},
                max_items=max_groups - 1,
                normalize=self._normalize_group,
                deadline=deadline,
                start_page=group_page,
                start_offset=group_offset,
            )
            groups.extend(group_pages.items)
        elif recursive:
            group_pages = PageResult((), True, group_page, group_offset)
        else:
            if "groups" in continuation:
                raise GitLabError("invalid_input")
            group_pages = PageResult((), False, None, None)

        project_params: dict[str, Any] = {
            "include_subgroups": str(recursive).lower(),
            "with_shared": str(include_shared).lower(),
            "archived": str(include_archived).lower(),
            "order_by": "path",
            "sort": "asc",
        }
        if search is not None:
            project_params["search"] = search
        project_pages = self._paginate(
            f"/api/v4/groups/{root['id']}/projects",
            params=project_params,
            max_items=max_projects,
            normalize=lambda item: self._normalize_group_project(
                item, root_path=root["full_path"]
            ),
            deadline=deadline,
            start_page=project_page,
            start_offset=project_offset,
        )
        projects = [
            project
            for project in project_pages.items
            if (include_shared or not project["shared"])
            and (include_archived or not project["archived"])
        ]
        project_counts: dict[str, int] = {}
        for project in projects:
            namespace = project["owning_namespace"]
            project_counts[namespace] = project_counts.get(namespace, 0) + 1
        normalized_groups = [
            {**value, "project_count": project_counts.get(value["full_path"], 0)}
            for value in groups
        ]
        normalized_groups.sort(key=lambda value: (value["full_path"], value["id"]))
        projects.sort(key=lambda value: (value["path_with_namespace"], value["id"]))
        next_sources = {
            source: value
            for source, value in (
                ("groups", self._continuation(group_pages)),
                ("projects", self._continuation(project_pages)),
            )
            if value is not None
        }
        truncated = group_pages.truncated or project_pages.truncated
        return {
            "root_group": next(
                value for value in normalized_groups if value["id"] == root["id"]
            ),
            "recursive": recursive,
            "include_shared": include_shared,
            "include_archived": include_archived,
            "search": search,
            "groups": normalized_groups,
            "projects": projects,
            "group_count": len(normalized_groups),
            "project_count": len(projects),
            "warnings": [],
            "truncated": truncated,
            "complete": not truncated,
            "continuation": next_sources or None,
        }

    @staticmethod
    def _project_summary(project: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": project["id"],
            "name": project["name"],
            "path_with_namespace": project["path_with_namespace"],
            "default_branch": project["default_branch"],
            "web_url": project["web_url"],
        }

    def _normalize_project_search_result(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        project_id = _remote_positive_int(payload.get("id"))
        name = _remote_text(payload, "name", 512)
        path = _namespace_path(payload.get("path_with_namespace"), remote=True)
        if "/" not in path:
            raise GitLabError("invalid_remote_data")
        description = payload.get("description")
        if description is not None:
            if not isinstance(description, str):
                raise GitLabError("invalid_remote_data")
            try:
                description = self._redact_text(description).encode("utf-8")
            except UnicodeEncodeError:
                raise GitLabError("invalid_remote_data") from None
            description = description[:_MAX_PROJECT_DESCRIPTION].decode(
                "utf-8", errors="ignore"
            )
        default_branch = payload.get("default_branch")
        if default_branch is not None:
            default_branch = _validate_remote_ref(default_branch)
        _, last_activity_at = _rfc3339(payload.get("last_activity_at"), remote=True)
        return {
            "id": project_id,
            "name": name,
            "path_with_namespace": path,
            "namespace": path.rsplit("/", 1)[0],
            "description": description,
            "default_branch": default_branch,
            "last_activity_at": last_activity_at,
            "web_url": _canonical_remote_url(
                payload.get("web_url"), self.client.auth.origin, f"/{path}"
            ),
        }

    def search_projects(
        self,
        query: str,
        *,
        max_items: int = 50,
        continuation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        query = _bounded_string(query, _MAX_SEARCH_QUERY)
        max_items = _positive_bound(max_items, _MAX_SEARCH_RESULTS)
        start_page, start_offset = _continuation_source(continuation)
        pages = self._paginate(
            "/api/v4/search",
            params={"scope": "projects", "search": query},
            max_items=max_items,
            normalize=self._normalize_project_search_result,
            start_page=start_page,
            start_offset=start_offset,
        )
        return {
            "query": query,
            "coverage": "visible_to_authenticated_user",
            "projects": list(pages.items),
            "count": len(pages.items),
            "truncated": pages.truncated,
            "complete": not pages.truncated,
            "continuation": self._continuation(pages),
        }

    def _normalize_code_match(
        self, payload: Mapping[str, Any], *, project: Mapping[str, Any]
    ) -> dict[str, Any]:
        project_id = _remote_positive_int(payload.get("project_id"))
        if project_id != project["id"]:
            raise GitLabError("invalid_remote_data")
        path = _remote_repo_path(payload.get("path"))
        filename = _remote_repo_path(payload.get("filename"))
        basename = _remote_repo_path(payload.get("basename"))
        if "/" in basename or filename != path or basename != path.rsplit("/", 1)[-1]:
            raise GitLabError("invalid_remote_data")
        ref = _validate_remote_ref(payload.get("ref"))
        start_line = payload.get("startline")
        if start_line is not None:
            start_line = _remote_positive_int(start_line)
        snippet = payload.get("data")
        if not isinstance(snippet, str):
            raise GitLabError("invalid_remote_data")
        try:
            snippet = self._redact_text(snippet).encode("utf-8")
        except UnicodeEncodeError:
            raise GitLabError("invalid_remote_data") from None
        result: dict[str, Any] = {
            "project": {"id": project_id, "path": project["path_with_namespace"]},
            "filename": basename,
            "path": path,
            "ref": ref,
            "snippet": snippet[:_MAX_SEARCH_SNIPPET_BYTES].decode(
                "utf-8", errors="ignore"
            ),
        }
        if start_line is not None:
            result["start_line"] = start_line
        return result

    def search_code(
        self,
        project: str | int,
        query: str,
        *,
        ref: str | None = None,
        max_items: int = 50,
        continuation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        query = _bounded_string(query, _MAX_SEARCH_QUERY)
        if ref is not None:
            ref = _validate_ref(ref)
        requested_max_items = _positive_bound(max_items, _MAX_SEARCH_RESULTS)
        start_page, start_offset = _continuation_source(continuation)
        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)
        applied_max_items = min(requested_max_items, _MAX_SEARCH_RESULTS_PER_CALL)
        params: dict[str, Any] = {"scope": "blobs", "search": query}
        if ref is not None:
            params["ref"] = ref
        pages = self._paginate(
            f"/api/v4/projects/{resolved['id']}/search",
            params=params,
            max_items=applied_max_items,
            normalize=lambda item: self._normalize_code_match(item, project=resolved),
            deadline=deadline,
            start_page=start_page,
            start_offset=start_offset,
        )
        return {
            "project": {"id": resolved["id"], "path": resolved["path_with_namespace"]},
            "query": query,
            "ref": ref,
            "requested_max_items": requested_max_items,
            "applied_max_items": applied_max_items,
            "matches": list(pages.items),
            "count": len(pages.items),
            "truncated": pages.truncated,
            "complete": not pages.truncated,
            "continuation": self._continuation(pages),
            "untrusted_content": True,
        }

    def _normalize_branch(
        self,
        payload: Mapping[str, Any],
        *,
        project_path: str,
    ) -> dict[str, Any]:
        name = _validate_remote_ref(payload.get("name"))
        flags = {}
        for field in (
            "default",
            "merged",
            "protected",
            "developers_can_push",
            "developers_can_merge",
            "can_push",
        ):
            value = payload.get(field)
            if not isinstance(value, bool):
                raise GitLabError("invalid_remote_data")
            flags[field] = value

        web_url = _same_origin_url(payload.get("web_url"), self.client.auth.origin)
        if unquote(urlsplit(web_url).path) != f"/{project_path}/-/tree/{name}":
            raise GitLabError("invalid_remote_data")
        web_url = (
            f"{self.client.auth.origin}/{quote(project_path, safe='/')}/-/tree/"
            f"{quote(name, safe='')}"
        )
        commit = _as_object(payload.get("commit"))
        sha = _commit_sha(commit.get("id"))
        short_sha = _commit_sha(commit.get("short_id"), short=True)
        if not sha.startswith(short_sha):
            raise GitLabError("invalid_remote_data")

        _committed, committed_at = _rfc3339(
            commit.get("committed_date"), remote=True
        )
        return {
            "name": name,
            "web_url": web_url,
            **flags,
            "commit": {
                "sha": sha,
                "short_sha": short_sha,
                "title": _remote_text(commit, "title", _MAX_COMMIT_TEXT),
                "committed_at": committed_at,
                "author_name": _remote_text(
                    commit, "author_name", 512, optional=True
                ),
                "committer_name": _remote_text(
                    commit, "committer_name", 512, optional=True
                ),
            },
        }

    def list_branches(
        self,
        project: str | int,
        *,
        search: str | None = None,
        max_items: int = 100,
        continuation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        max_items = _positive_bound(max_items, _MAX_BRANCHES)
        if search is not None:
            search = _bounded_string(search, _MAX_SEARCH_QUERY)
        start_page, start_offset = _continuation_source(continuation)

        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)
        params: dict[str, Any] = {}
        if search is not None:
            params["search"] = search
        pages = self._paginate(
            f"/api/v4/projects/{resolved['id']}/repository/branches",
            params=params,
            max_items=max_items,
            normalize=lambda item: self._normalize_branch(
                item, project_path=resolved["path_with_namespace"]
            ),
            deadline=deadline,
            start_page=start_page,
            start_offset=start_offset,
        )
        return {
            "project": self._project_summary(resolved),
            "filters": {"search": search},
            "branches": list(pages.items),
            "count": len(pages.items),
            "truncated": pages.truncated,
            "continuation": self._continuation(pages),
        }

    def _normalize_tag(
        self,
        payload: Mapping[str, Any],
        *,
        project_path: str,
    ) -> dict[str, Any]:
        name = _validate_remote_ref(payload.get("name"))
        target = _commit_sha(payload.get("target"))
        message = payload.get("message")
        if message is not None:
            if not isinstance(message, str) or "\x00" in message:
                raise GitLabError("invalid_remote_data")
            try:
                redacted = self._redact_text(message).encode("utf-8")
            except UnicodeEncodeError:
                raise GitLabError("invalid_remote_data") from None
            message = redacted[:_MAX_TAG_MESSAGE].decode("utf-8", errors="ignore")
        protected = payload.get("protected")
        if not isinstance(protected, bool):
            raise GitLabError("invalid_remote_data")
        created_at = payload.get("created_at")
        if created_at is not None:
            _created, created_at = _rfc3339(created_at, remote=True)

        commit = _as_object(payload.get("commit"))
        sha = _commit_sha(commit.get("id"))
        short_sha = _commit_sha(commit.get("short_id"), short=True)
        if target != sha or not sha.startswith(short_sha):
            raise GitLabError("invalid_remote_data")

        _committed, committed_at = _rfc3339(
            commit.get("committed_date"), remote=True
        )
        return {
            "name": name,
            "target": target,
            "message": message,
            "protected": protected,
            "created_at": created_at,
            "web_url": (
                f"{self.client.auth.origin}/{quote(project_path, safe='/')}/-/tags/"
                f"{quote(name, safe='')}"
            ),
            "commit": {
                "sha": sha,
                "short_sha": short_sha,
                "title": _remote_text(commit, "title", _MAX_COMMIT_TEXT),
                "committed_at": committed_at,
                "author_name": _remote_text(
                    commit, "author_name", 512, optional=True
                ),
                "committer_name": _remote_text(
                    commit, "committer_name", 512, optional=True
                ),
            },
        }

    def _normalize_release_summary(
        self,
        payload: Mapping[str, Any],
        *,
        project_path: str,
    ) -> dict[str, Any]:
        tag = _validate_remote_ref(payload.get("tag_name"))
        if len(tag.encode("utf-8")) > _MAX_REF:
            raise GitLabError("invalid_remote_data")
        name = _remote_text(payload, "name", _MAX_REF)
        if len(name.encode("utf-8")) > _MAX_REF:
            raise GitLabError("invalid_remote_data")
        _created, created_at = _rfc3339(payload.get("created_at"), remote=True)
        _released, released_at = _rfc3339(payload.get("released_at"), remote=True)
        upcoming_release = payload.get("upcoming_release")
        if not isinstance(upcoming_release, bool):
            raise GitLabError("invalid_remote_data")
        milestones = _as_list(payload.get("milestones"))
        if len(milestones) > _MAX_RELEASE_MILESTONES:
            raise GitLabError("invalid_remote_data")
        assets = _as_object(payload.get("assets"))
        asset_count = assets.get("count")
        if (
            isinstance(asset_count, bool)
            or not isinstance(asset_count, int)
            or not 0 <= asset_count <= _MAX_RELEASE_ASSETS
        ):
            raise GitLabError("invalid_remote_data")
        links = _as_object(payload.get("_links"))
        _same_origin_url(links.get("self"), self.client.auth.origin)

        result: dict[str, Any] = {
            "tag": tag,
            "name": self._redact_text(name),
            "created_at": created_at,
            "released_at": released_at,
            "upcoming_release": upcoming_release,
            "web_url": (
                f"{self.client.auth.origin}/{quote(project_path, safe='/')}/-/releases/"
                f"{quote(tag, safe='')}"
            ),
            "milestone_count": len(milestones),
            "asset_count": asset_count,
        }
        description = payload.get("description")
        if description is not None:
            if not isinstance(description, str) or "\x00" in description:
                raise GitLabError("invalid_remote_data")
            try:
                encoded_description = description.encode("utf-8")
            except UnicodeEncodeError:
                raise GitLabError("invalid_remote_data") from None
            if len(encoded_description) > _MAX_RELEASE_DESCRIPTION:
                raise GitLabError("invalid_remote_data")
            result["description_summary"] = self._redact_text(description).encode(
                "utf-8"
            )[:_MAX_RELEASE_SUMMARY_BYTES].decode("utf-8", errors="ignore")
        author = payload.get("author")
        if author is not None:
            result["author"] = {
                field: value if field == "id" else self._redact_text(value)
                for field, value in self._normalize_user(author).items()
            }
        commit = payload.get("commit")
        if commit is not None:
            commit = _as_object(commit)
            sha = _commit_sha(commit.get("id"))
            short_sha = _commit_sha(commit.get("short_id"), short=True)
            if not sha.startswith(short_sha):
                raise GitLabError("invalid_remote_data")
            result["commit"] = {"sha": sha, "short_sha": short_sha}
        return result

    def _release_asset_url(self, value: Any) -> str | None:
        """Return a safe same-origin asset URL, or omit a foreign absolute URL."""
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or "\x00" in value
        ):
            raise GitLabError("invalid_remote_data")
        try:
            if len(value.encode("utf-8")) > _MAX_PROJECT_REFERENCE:
                raise GitLabError("invalid_remote_data")
        except UnicodeEncodeError:
            raise GitLabError("invalid_remote_data") from None
        try:
            parsed = urlsplit(value)
            configured = urlsplit(self.client.auth.origin)
            port = parsed.port
            configured_port = configured.port
        except ValueError:
            raise GitLabError("invalid_remote_data") from None
        if not parsed.scheme or not parsed.hostname:
            raise GitLabError("invalid_remote_data")
        if (
            parsed.scheme != configured.scheme
            or parsed.hostname != configured.hostname
            or port != configured_port
        ):
            return None
        return _same_origin_url(value, self.client.auth.origin)

    def _normalize_release_milestone(self, payload: Any) -> dict[str, Any]:
        milestone = _as_object(payload)
        result: dict[str, Any] = {"id": _remote_positive_int(milestone.get("id"))}
        iid = milestone.get("iid")
        if iid is not None:
            result["iid"] = _remote_positive_int(iid)
        for field, maximum in (("title", 512), ("state", 64)):
            value = milestone.get(field)
            if value is not None:
                result[field] = self._remote_display_text(
                    milestone, field, maximum
                )
        return result

    def _normalize_release_detail(
        self,
        payload: Mapping[str, Any],
        *,
        project_path: str,
    ) -> dict[str, Any]:
        milestones = _as_list(payload.get("milestones"))
        assets = _as_object(payload.get("assets"))
        sources = _as_list(assets.get("sources"))
        links = _as_list(assets.get("links"))
        summary_payload = dict(payload)
        summary_payload["milestones"] = milestones[:_MAX_RELEASE_MILESTONES]
        summary = self._normalize_release_summary(
            summary_payload, project_path=project_path
        )
        result = {
            field: value
            for field, value in summary.items()
            if field != "description_summary"
        }
        description = payload.get("description")
        if description is not None:
            if not isinstance(description, str) or "\x00" in description:
                raise GitLabError("invalid_remote_data")
            try:
                if len(description.encode("utf-8")) > _MAX_RELEASE_DESCRIPTION:
                    raise GitLabError("invalid_remote_data")
                description = self._redact_text(description)
                if len(description.encode("utf-8")) > _MAX_RELEASE_DESCRIPTION:
                    raise GitLabError("invalid_remote_data")
            except UnicodeEncodeError:
                raise GitLabError("invalid_remote_data") from None
            result["description"] = description

        normalized_milestones = [
            self._normalize_release_milestone(item)
            for item in milestones[:_MAX_RELEASE_MILESTONES]
        ]
        result.update(
            {
                "milestones": normalized_milestones,
                "milestone_count": len(milestones),
                "milestone_returned": len(normalized_milestones),
                "milestone_truncated": len(milestones) > _MAX_RELEASE_MILESTONES,
            }
        )

        external_urls_omitted = 0
        normalized_sources = []
        for item in sources[:_MAX_RELEASE_ASSETS]:
            source = _as_object(item)
            url = self._release_asset_url(source.get("url"))
            if url is None:
                external_urls_omitted += 1
                continue
            normalized = {"url": url}
            if source.get("format") is not None:
                normalized["format"] = self._remote_display_text(
                    source, "format", 64
                )
            normalized_sources.append(normalized)

        normalized_links = []
        for item in links[:_MAX_RELEASE_ASSETS]:
            link = _as_object(item)
            url = self._release_asset_url(link.get("url"))
            direct_url = None
            if link.get("direct_asset_url") is not None:
                direct_url = self._release_asset_url(link.get("direct_asset_url"))
                if direct_url is None:
                    external_urls_omitted += 1
            if url is None:
                external_urls_omitted += 1
                continue
            normalized = {
                "id": _remote_positive_int(link.get("id")),
                "name": self._remote_display_text(link, "name", 512),
                "link_type": self._remote_display_text(link, "link_type", 64),
                "url": url,
            }
            if direct_url is not None:
                normalized["direct_asset_url"] = direct_url
            normalized_links.append(normalized)

        result["assets"] = {
            "sources": normalized_sources,
            "source_count": len(sources),
            "source_returned": len(normalized_sources),
            "source_truncated": len(sources) > _MAX_RELEASE_ASSETS,
            "links": normalized_links,
            "link_count": len(links),
            "link_returned": len(normalized_links),
            "link_truncated": len(links) > _MAX_RELEASE_ASSETS,
            "external_urls_omitted": external_urls_omitted,
        }
        result["warnings"] = (
            ["external_asset_links_omitted"] if external_urls_omitted else []
        )
        return result

    def read_release(self, project: str | int, tag: str) -> dict[str, Any]:
        tag = _validate_ref(tag)
        try:
            if len(tag.encode("utf-8")) > _MAX_REF:
                raise GitLabError("invalid_input")
        except UnicodeEncodeError:
            raise GitLabError("invalid_input") from None
        self._parse_project_reference(project)
        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)
        payload = _as_object(
            self.client.get_json(
                f"/api/v4/projects/{resolved['id']}/releases/{quote(tag, safe='')}",
                deadline=deadline,
            )
        )
        detail = self._normalize_release_detail(
            payload, project_path=resolved["path_with_namespace"]
        )
        if detail["tag"] != tag:
            raise GitLabError("invalid_remote_data")
        link = _as_object(payload.get("_links")).get("self")
        expected_path = f"/api/v4/projects/{resolved['id']}/releases/{tag}"
        if unquote(urlsplit(link).path) != expected_path:
            raise GitLabError("invalid_remote_data")
        return {"project": self._project_summary(resolved), "release": detail}

    def list_releases(
        self,
        project: str | int,
        *,
        order_by: str = "released_at",
        sort: str = "desc",
        max_items: int = 50,
        continuation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if isinstance(project, str):
            try:
                if len(project.encode("utf-8")) > _MAX_PROJECT_REFERENCE:
                    raise GitLabError("invalid_input")
            except UnicodeEncodeError:
                raise GitLabError("invalid_input") from None
        self._parse_project_reference(project)
        if (
            not isinstance(order_by, str)
            or order_by not in {"released_at", "created_at"}
            or not isinstance(sort, str)
            or sort not in {"asc", "desc"}
        ):
            raise GitLabError("invalid_input")
        max_items = _positive_bound(max_items, _MAX_RELEASES)
        start_page, start_offset = _continuation_source(continuation)

        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)

        def normalize(item: Mapping[str, Any]) -> dict[str, Any]:
            result = self._normalize_release_summary(
                item, project_path=resolved["path_with_namespace"]
            )
            link = _as_object(item.get("_links")).get("self")
            expected_path = (
                f"/api/v4/projects/{resolved['id']}/releases/{result['tag']}"
            )
            if unquote(urlsplit(link).path) != expected_path:
                raise GitLabError("invalid_remote_data")
            return result

        pages = self._paginate(
            f"/api/v4/projects/{resolved['id']}/releases",
            params={"order_by": order_by, "sort": sort},
            max_items=max_items,
            normalize=normalize,
            deadline=deadline,
            start_page=start_page,
            start_offset=start_offset,
        )
        return {
            "project": self._project_summary(resolved),
            "filters": {"order_by": order_by, "sort": sort},
            "releases": list(pages.items),
            "count": len(pages.items),
            "truncated": pages.truncated,
            "continuation": self._continuation(pages),
        }

    def _normalize_todo_identity(
        self,
        payload: Any,
        *,
        path_field: str,
        url_prefix: str,
    ) -> dict[str, Any] | None:
        if payload is None:
            return None
        source = _as_object(payload)
        path = _namespace_path(source.get(path_field), remote=True)
        result: dict[str, Any] = {
            "id": _remote_positive_int(source.get("id")),
            path_field: path,
        }
        name = source.get("name")
        if name is not None:
            result["name"] = self._remote_display_text(source, "name", 512)
        expected_path = f"{url_prefix}{path}"
        web_url = source.get("web_url")
        result["web_url"] = (
            f"{self.client.auth.origin}{quote(expected_path, safe='/')}"
            if web_url is None
            else _canonical_remote_url(web_url, self.client.auth.origin, expected_path)
        )
        return result

    def _normalize_todo_target(
        self,
        payload: Mapping[str, Any],
        *,
        target_type: str,
        project: dict[str, Any] | None,
        group: dict[str, Any] | None,
    ) -> dict[str, Any]:
        target = _as_object(payload.get("target"))
        target_url = _same_origin_url(payload.get("target_url"), self.client.auth.origin)
        result: dict[str, Any] = {}
        if target_type == "Commit":
            if target.get("iid") is not None:
                raise GitLabError("invalid_remote_data")
            result["sha"] = _commit_sha(target.get("id"))
            if target.get("sha") is not None and _commit_sha(target.get("sha")) != result["sha"]:
                raise GitLabError("invalid_remote_data")
        else:
            result["id"] = _remote_positive_int(target.get("id"))
            iid = target.get("iid")
            if target_type in _TODO_IID_TARGET_TYPES:
                result["iid"] = _remote_positive_int(iid)
            elif target_type in _TODO_TARGET_TYPES and iid is not None:
                raise GitLabError("invalid_remote_data")
            elif iid is not None:
                result["iid"] = _remote_positive_int(iid)
        for field in ("title", "name", "state"):
            value = target.get(field)
            if value is not None:
                result[field] = self._remote_display_text(target, field, 512)

        expected_path = None
        target_path = unquote(urlsplit(target_url).path)
        project_path = project and project["path_with_namespace"]
        group_path = group and group["full_path"]
        suffixes = {
            "Issue": f"/-/issues/{result.get('iid')}",
            "MergeRequest": f"/-/merge_requests/{result.get('iid')}",
            "Commit": f"/-/commit/{result.get('sha')}",
            "Epic": f"/-/epics/{result.get('iid')}",
            "DesignManagement::Design": f"/-/designs/{result.get('iid')}",
            "AlertManagement::Alert": f"/-/alert_management/alerts/{result.get('id')}",
            "Vulnerability": f"/-/security/vulnerabilities/{result.get('id')}",
            "WikiPage::Meta": f"/-/wikis/{result.get('id')}",
        }
        if target_type in suffixes:
            suffix = suffixes[target_type]
            if not target_path.endswith(suffix):
                raise GitLabError("invalid_remote_data")
            parent_path = target_path[: -len(suffix)]
            if target_type == "Epic":
                if not parent_path.startswith("/groups/"):
                    raise GitLabError("invalid_remote_data")
                path = _namespace_path(parent_path.removeprefix("/groups/"), remote=True)
                if group_path is not None and path != group_path:
                    raise GitLabError("invalid_remote_data")
                expected_path = f"/groups/{group_path or path}{suffix}"
            else:
                if not parent_path.startswith("/"):
                    raise GitLabError("invalid_remote_data")
                path = _namespace_path(parent_path[1:], remote=True)
                if project_path is not None and path != project_path:
                    raise GitLabError("invalid_remote_data")
                expected_path = f"/{project_path or path}{suffix}"
        elif target_type == "Project":
            if not target_path.startswith("/") or "/-/" in target_path:
                raise GitLabError("invalid_remote_data")
            path = _namespace_path(target_path[1:], remote=True)
            if project is not None and (
                result["id"] != project["id"] or path != project_path
            ):
                raise GitLabError("invalid_remote_data")
            expected_path = f"/{project_path or path}"
        elif target_type == "Namespace":
            if not target_path.startswith("/groups/") or "/-/" in target_path:
                raise GitLabError("invalid_remote_data")
            path = _namespace_path(target_path.removeprefix("/groups/"), remote=True)
            if group is not None and (
                result["id"] != group["id"] or path != group_path
            ):
                raise GitLabError("invalid_remote_data")
            expected_path = f"/groups/{group_path or path}"
        if expected_path is not None:
            target_url = _canonical_remote_url(
                target_url, self.client.auth.origin, expected_path
            )
        result["web_url"] = target_url
        return result

    def _normalize_todo(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        todo_id = _remote_positive_int(payload.get("id"))
        action_name = _remote_open_text(payload.get("action_name"), _MAX_REF)
        state = _remote_open_text(payload.get("state"), 64)
        if state not in _TODO_STATES:
            raise GitLabError("invalid_remote_data")
        _created, created_at = _rfc3339(payload.get("created_at"), remote=True)
        _updated, updated_at = _rfc3339(payload.get("updated_at"), remote=True)
        project = self._normalize_todo_identity(
            payload.get("project"), path_field="path_with_namespace", url_prefix="/"
        )
        group = self._normalize_todo_identity(
            payload.get("group"), path_field="full_path", url_prefix="/groups/"
        )
        target_type = _remote_open_text(payload.get("target_type"), _MAX_REF)
        result: dict[str, Any] = {
            "id": todo_id,
            "action_name": action_name,
            "state": state,
            "author": self._normalize_user(payload.get("author")),
            "created_at": created_at,
            "updated_at": updated_at,
            "target_type": target_type,
            "target": self._normalize_todo_target(
                payload, target_type=target_type, project=project, group=group
            ),
        }
        if project is not None:
            result["project"] = project
        if group is not None:
            result["group"] = group
        return result

    def list_todos(
        self,
        *,
        project: str | int | None = None,
        state: str = "pending",
        action: str | None = None,
        target_type: str | None = None,
        max_items: int = 100,
        continuation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = _bounded_enum(state, _TODO_STATES, 64)
        if action is not None:
            action = _bounded_enum(action, _TODO_ACTIONS, _MAX_REF)
        if target_type is not None:
            target_type = _bounded_enum(target_type, _TODO_TARGET_TYPES, _MAX_REF)
        max_items = _positive_bound(max_items, _MAX_TODOS)
        start_page, start_offset = _continuation_source(continuation)
        if project is not None:
            if isinstance(project, bool) or (
                isinstance(project, int) and project <= 0
            ):
                raise GitLabError("invalid_input")
            if isinstance(project, str):
                try:
                    if len(project.encode("utf-8")) > _MAX_PROJECT_REFERENCE:
                        raise GitLabError("invalid_input")
                except UnicodeEncodeError:
                    raise GitLabError("invalid_input") from None
            self._parse_project_reference(project)

        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline) if project is not None else None
        params: dict[str, Any] = {"state": state}
        if action is not None:
            params["action"] = action
        if target_type is not None:
            params["type"] = target_type
        if resolved is not None:
            params["project_id"] = resolved["id"]

        def normalize(item: Mapping[str, Any]) -> dict[str, Any]:
            todo = self._normalize_todo(item)
            if (
                resolved is not None
                and todo.get("project") is not None
                and todo["project"]["id"] != resolved["id"]
            ):
                raise GitLabError("invalid_remote_data")
            return todo

        pages = self._paginate(
            "/api/v4/todos",
            params=params,
            max_items=max_items,
            normalize=normalize,
            deadline=deadline,
            start_page=start_page,
            start_offset=start_offset,
        )
        return {
            "filters": {
                "state": state,
                "action": action,
                "target_type": target_type,
                "project_id": resolved["id"] if resolved is not None else None,
            },
            "todos": list(pages.items),
            "count": len(pages.items),
            "truncated": pages.truncated,
            "continuation": self._continuation(pages),
            "untrusted_content": True,
        }

    def list_tags(
        self,
        project: str | int,
        *,
        search: str | None = None,
        order_by: str = "name",
        sort: str = "asc",
        max_items: int = 100,
        continuation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        max_items = _positive_bound(max_items, _MAX_TAGS)
        if search is not None:
            search = _bounded_string(search, _MAX_SEARCH_QUERY)
        if (
            not isinstance(order_by, str)
            or order_by not in {"name", "updated"}
            or not isinstance(sort, str)
            or sort not in {"asc", "desc"}
        ):
            raise GitLabError("invalid_input")
        start_page, start_offset = _continuation_source(continuation)

        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)
        params: dict[str, Any] = {"order_by": order_by, "sort": sort}
        if search is not None:
            params["search"] = search
        pages = self._paginate(
            f"/api/v4/projects/{resolved['id']}/repository/tags",
            params=params,
            max_items=max_items,
            normalize=lambda item: self._normalize_tag(
                item, project_path=resolved["path_with_namespace"]
            ),
            deadline=deadline,
            start_page=start_page,
            start_offset=start_offset,
        )
        return {
            "project": self._project_summary(resolved),
            "filters": {"search": search, "order_by": order_by, "sort": sort},
            "tags": list(pages.items),
            "count": len(pages.items),
            "truncated": pages.truncated,
            "continuation": self._continuation(pages),
        }

    def _normalize_commit(
        self,
        payload: Mapping[str, Any],
        *,
        project_path: str,
        include_stats: bool = False,
    ) -> dict[str, Any]:
        sha = _commit_sha(payload.get("id"))
        short_sha = _commit_sha(payload.get("short_id"), short=True)
        if not sha.startswith(short_sha):
            raise GitLabError("invalid_remote_data")
        title = payload.get("title")
        message = payload.get("message")
        author_name = payload.get("author_name")
        committer_name = payload.get("committer_name")
        if any(
            not isinstance(value, str)
            or not value
            or "\x00" in value
            or len(value) > maximum
            for value, maximum in (
                (title, _MAX_COMMIT_TEXT),
                (message, _MAX_COMMIT_TEXT),
                (author_name, 512),
                (committer_name, 512),
            )
        ):
            raise GitLabError("invalid_remote_data")
        _authored, authored_at = _rfc3339(payload.get("authored_date"), remote=True)
        _committed, committed_at = _rfc3339(
            payload.get("committed_date"), remote=True
        )
        _created, created_at = _rfc3339(payload.get("created_at"), remote=True)
        parent_values = _as_list(payload.get("parent_ids"))
        if len(parent_values) > 100:
            raise GitLabError("invalid_remote_data")
        parents = [_commit_sha(value) for value in parent_values]
        web_url = _canonical_remote_url(
            payload.get("web_url"),
            self.client.auth.origin,
            f"/{project_path}/-/commit/{sha}",
        )
        result: dict[str, Any] = {
            "sha": sha,
            "short_sha": short_sha,
            "title": title,
            "message": message,
            "author_name": author_name,
            "committer_name": committer_name,
            "authored_at": authored_at,
            "committed_at": committed_at,
            "created_at": created_at,
            "parent_shas": parents,
            "web_url": web_url,
        }
        if include_stats:
            stats = _as_object(payload.get("stats"))
            if set(stats) != {"additions", "deletions", "total"}:
                raise GitLabError("invalid_remote_data")
            normalized_stats: dict[str, int] = {}
            for field in ("additions", "deletions", "total"):
                value = stats.get(field)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not 0 <= value <= _MAX_COMMIT_STAT
                ):
                    raise GitLabError("invalid_remote_data")
                normalized_stats[field] = value
            if normalized_stats["total"] != (
                normalized_stats["additions"] + normalized_stats["deletions"]
            ):
                raise GitLabError("invalid_remote_data")
            result["stats"] = normalized_stats
        return result

    def _normalize_job(
        self,
        payload: Mapping[str, Any],
        *,
        project_path: str,
        expected_id: int | None = None,
    ) -> dict[str, Any]:
        """Project one GitLab job without trace, artifacts, variables, or email."""
        identifier = _remote_positive_int(payload.get("id"))
        if expected_id is not None and identifier != expected_id:
            raise GitLabError("invalid_remote_data")

        def required_text(source: Mapping[str, Any], field: str, maximum: int) -> str:
            value = source.get(field)
            if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
                raise GitLabError("invalid_remote_data")
            return value

        def optional_time(field: str) -> str | None:
            value = payload.get(field)
            if value is None:
                return None
            _parsed, normalized = _rfc3339(value, remote=True)
            return normalized

        def optional_duration(field: str) -> float | None:
            value = payload.get(field)
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise GitLabError("invalid_remote_data")
            try:
                normalized = float(value)
            except OverflowError:
                raise GitLabError("invalid_remote_data") from None
            if normalized < 0 or not math.isfinite(normalized):
                raise GitLabError("invalid_remote_data")
            return normalized

        tag = payload.get("tag")
        allow_failure = payload.get("allow_failure")
        if not isinstance(tag, bool) or not isinstance(allow_failure, bool):
            raise GitLabError("invalid_remote_data")
        pipeline_raw = _as_object(payload.get("pipeline"))
        commit_raw = _as_object(payload.get("commit"))
        pipeline_id = _remote_positive_int(pipeline_raw.get("id"))
        commit_sha = _commit_sha(commit_raw.get("id"))
        short_sha = _commit_sha(commit_raw.get("short_id"), short=True)
        if not commit_sha.startswith(short_sha):
            raise GitLabError("invalid_remote_data")
        encoded_project = quote(project_path, safe="/")
        pipeline_url = (
            f"{self.client.auth.origin}/{encoded_project}/-/pipelines/{pipeline_id}"
        )
        commit_url = (
            f"{self.client.auth.origin}/{encoded_project}/-/commit/{commit_sha}"
        )
        web_url = _same_origin_url(
            required_text(payload, "web_url", _MAX_PROJECT_REFERENCE),
            self.client.auth.origin,
        )
        failure_reason = payload.get("failure_reason")
        if failure_reason is not None and (
            not isinstance(failure_reason, str)
            or len(failure_reason) > _MAX_JOB_TEXT
            or "\x00" in failure_reason
        ):
            raise GitLabError("invalid_remote_data")
        return {
            "id": identifier,
            "name": required_text(payload, "name", _MAX_JOB_TEXT),
            "stage": required_text(payload, "stage", _MAX_JOB_TEXT),
            "status": required_text(payload, "status", 64),
            "ref": _validate_remote_ref(payload.get("ref")),
            "tag": tag,
            "allow_failure": allow_failure,
            "created_at": optional_time("created_at"),
            "queued_at": optional_time("queued_at"),
            "started_at": optional_time("started_at"),
            "finished_at": optional_time("finished_at"),
            "erased_at": optional_time("erased_at"),
            "duration": optional_duration("duration"),
            "queued_duration": optional_duration("queued_duration"),
            "failure_reason": failure_reason,
            "pipeline": {
                "id": pipeline_id,
                "status": required_text(pipeline_raw, "status", 64),
                "web_url": pipeline_url,
            },
            "commit": {
                "sha": commit_sha,
                "short_sha": short_sha,
                "title": required_text(commit_raw, "title", _MAX_COMMIT_TEXT),
                "web_url": commit_url,
            },
            "user": (
                None if payload.get("user") is None
                else self._normalize_user(payload.get("user"))
            ),
            "web_url": web_url,
        }

    def list_commits(
        self,
        project: str | int,
        *,
        ref: str | None = None,
        path: str | None = None,
        since: str | None = None,
        until: str | None = None,
        lookback_hours: int | None = None,
        max_items: int = 100,
        continuation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        max_items = _positive_bound(max_items, _MAX_COMMITS)
        if ref is not None:
            ref = _validate_ref(ref)
        if path is not None:
            path = _validate_path(path, allow_empty=False)
        if lookback_hours is not None:
            lookback_hours = _positive_bound(lookback_hours, 24 * 365)
            if since is not None:
                raise GitLabError("invalid_input")
            now = self._now()
            if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
                raise GitLabError("invalid_configuration")
            since_dt = now.astimezone(timezone.utc) - timedelta(hours=lookback_hours)
            since = since_dt.isoformat().replace("+00:00", "Z")
        since_dt = None
        until_dt = None
        if since is not None:
            since_dt, since = _rfc3339(since, remote=False)
        if until is not None:
            until_dt, until = _rfc3339(until, remote=False)
        if since_dt is not None and until_dt is not None and since_dt > until_dt:
            raise GitLabError("invalid_input")
        start_page, start_offset = _continuation_source(continuation)

        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)
        selected_ref = ref or resolved["default_branch"]
        params: dict[str, Any] = {
            "ref_name": selected_ref,
            "order": "default",
        }
        if path is not None:
            params["path"] = path
        if since is not None:
            params["since"] = since
        if until is not None:
            params["until"] = until
        pages = self._paginate(
            f"/api/v4/projects/{resolved['id']}/repository/commits",
            params=params,
            max_items=max_items,
            normalize=lambda item: self._normalize_commit(
                item, project_path=resolved["path_with_namespace"]
            ),
            deadline=deadline,
            start_page=start_page,
            start_offset=start_offset,
        )
        return {
            "project": self._project_summary(resolved),
            "ref": selected_ref,
            "path": path,
            "time_window": {
                "since": since,
                "until": until,
                "lookback_hours": lookback_hours,
            },
            "commits": list(pages.items),
            "count": len(pages.items),
            "truncated": pages.truncated,
            "continuation": self._continuation(pages),
        }

    def read_commit(
        self,
        project: str | int,
        commit: str,
    ) -> dict[str, Any]:
        commit = _validate_ref(_bounded_string(commit, _MAX_REF))
        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)
        payload = _as_object(
            self.client.get_json(
                f"/api/v4/projects/{resolved['id']}/repository/commits/{quote(commit, safe='')}",
                params={"stats": "true"},
                deadline=deadline,
            )
        )
        return {
            "project": self._project_summary(resolved),
            "requested_commit": commit,
            "commit": self._normalize_commit(
                payload,
                project_path=resolved["path_with_namespace"],
                include_stats=True,
            ),
        }

    def read_job(self, project: str | int, job_id: int) -> dict[str, Any]:
        job_id = _positive_bound(job_id, 2_147_483_647)
        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)
        payload = _as_object(self.client.get_json(
            f"/api/v4/projects/{resolved['id']}/jobs/{job_id}",
            deadline=deadline,
        ))
        return {
            "project": {"id": resolved["id"], "path": resolved["path_with_namespace"]},
            "job": self._normalize_job(
                payload,
                project_path=resolved["path_with_namespace"],
                expected_id=job_id,
            ),
        }

    def list_pipeline_jobs(
        self,
        project: str | int,
        pipeline_id: int,
        *,
        statuses: list[str] | None = None,
        include_retried: bool = False,
        max_items: int = 100,
        continuation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        pipeline_id = _positive_bound(pipeline_id, 2_147_483_647)
        max_items = _positive_bound(max_items, _MAX_JOBS)
        if type(include_retried) is not bool:
            raise GitLabError("invalid_input")
        start_page, start_offset = _continuation_source(continuation)
        if statuses is not None:
            allowed_statuses = {
                "created", "waiting_for_callback", "waiting_for_resource",
                "preparing", "pending", "running", "success", "failed",
                "canceled", "canceling", "skipped", "manual", "scheduled",
            }
            if (
                not isinstance(statuses, list)
                or not statuses
                or any(
                    not isinstance(status, str) or status not in allowed_statuses
                    for status in statuses
                )
                or len(statuses) != len(set(statuses))
            ):
                raise GitLabError("invalid_input")
        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)
        params: dict[str, Any] = {"include_retried": include_retried}
        if statuses is not None:
            params["scope[]"] = statuses
        pages = self._paginate(
            f"/api/v4/projects/{resolved['id']}/pipelines/{pipeline_id}/jobs",
            params=params,
            max_items=max_items,
            normalize=lambda item: self._normalize_job(
                item, project_path=resolved["path_with_namespace"]
            ),
            deadline=deadline,
            start_page=start_page,
            start_offset=start_offset,
        )
        return {
            "project": {"id": resolved["id"], "path": resolved["path_with_namespace"]},
            "pipeline": {"id": pipeline_id},
            "statuses": statuses,
            "include_retried": include_retried,
            "jobs": list(pages.items),
            "count": len(pages.items),
            "truncated": pages.truncated,
            "continuation": self._continuation(pages),
        }

    @staticmethod
    def _normalize_user(payload: Any) -> dict[str, Any]:
        user = _as_object(payload)
        user_id = _remote_positive_int(user.get("id"))
        values: dict[str, str] = {}
        for field, maximum in (("username", 255), ("name", 512), ("state", 64)):
            value = user.get(field)
            try:
                value_bytes = value.encode("utf-8") if isinstance(value, str) else b""
            except UnicodeEncodeError:
                raise GitLabError("invalid_remote_data") from None
            if (
                not isinstance(value, str)
                or not value
                or value != value.strip()
                or len(value_bytes) > maximum
                or "\x00" in value
            ):
                raise GitLabError("invalid_remote_data")
            values[field] = value
        return {"id": user_id, **values}

    def _normalize_commit_comment(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = payload.get("note")
        if (
            not isinstance(body, str)
            or len(body) > _MAX_NOTE_BODY
            or "\x00" in body
        ):
            raise GitLabError("invalid_remote_data")
        _created, created_at = _rfc3339(payload.get("created_at"), remote=True)
        path = payload.get("path")
        line = payload.get("line")
        line_type = payload.get("line_type")
        if path is not None:
            path = _remote_repo_path(path)
        if line is not None and (
            isinstance(line, bool) or not isinstance(line, int) or line <= 0
        ):
            raise GitLabError("invalid_remote_data")
        if line_type not in {None, "new", "old"}:
            raise GitLabError("invalid_remote_data")
        if (path is None) != (line is None) or (line is None) != (line_type is None):
            raise GitLabError("invalid_remote_data")
        return {
            "body": body,
            "author": self._normalize_user(payload.get("author")),
            "created_at": created_at,
            "path": path,
            "line": line,
            "line_type": line_type,
        }

    @staticmethod
    def _normalize_position(payload: Any) -> dict[str, Any] | None:
        if payload is None:
            return None
        position = _as_object(payload)
        if position.get("position_type") != "text":
            raise GitLabError("invalid_remote_data")
        output: dict[str, Any] = {"position_type": "text"}
        for field in ("base_sha", "start_sha", "head_sha"):
            output[field] = _commit_sha(position.get(field))
        for field in ("old_path", "new_path"):
            output[field] = _remote_repo_path(position.get(field))
        for field in ("old_line", "new_line"):
            value = position.get(field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise GitLabError("invalid_remote_data")
            output[field] = value
        if output["old_line"] is None and output["new_line"] is None:
            raise GitLabError("invalid_remote_data")
        return output

    def _normalize_note(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        note_id = _remote_positive_int(payload.get("id"))
        body = payload.get("body")
        if (
            not isinstance(body, str)
            or len(body) > _MAX_NOTE_BODY
            or "\x00" in body
        ):
            raise GitLabError("invalid_remote_data")
        _created, created_at = _rfc3339(payload.get("created_at"), remote=True)
        _updated, updated_at = _rfc3339(payload.get("updated_at"), remote=True)
        system = payload.get("system")
        resolvable = payload.get("resolvable")
        resolved = payload.get("resolved")
        if not all(isinstance(value, bool) for value in (system, resolvable, resolved)):
            raise GitLabError("invalid_remote_data")
        resolved_by_payload = payload.get("resolved_by")
        resolved_by = (
            None
            if resolved_by_payload is None
            else self._normalize_user(resolved_by_payload)
        )
        resolved_at_payload = payload.get("resolved_at")
        if resolved_at_payload is None:
            resolved_at = None
        else:
            _resolved_at, resolved_at = _rfc3339(
                resolved_at_payload, remote=True
            )
        if resolved and (resolved_by is None or resolved_at is None):
            raise GitLabError("invalid_remote_data")
        if not resolved and (resolved_by is not None or resolved_at is not None):
            raise GitLabError("invalid_remote_data")
        return {
            "id": note_id,
            "body": body,
            "author": self._normalize_user(payload.get("author")),
            "created_at": created_at,
            "updated_at": updated_at,
            "system": system,
            "resolvable": resolvable,
            "resolved": resolved,
            "resolved_by": resolved_by,
            "resolved_at": resolved_at,
            "position": self._normalize_position(payload.get("position")),
        }

    def _normalize_discussion(
        self,
        payload: Mapping[str, Any],
        *,
        max_notes: int,
    ) -> dict[str, Any]:
        discussion_id = payload.get("id")
        individual_note = payload.get("individual_note")
        if (
            not isinstance(discussion_id, str)
            or not discussion_id
            or len(discussion_id) > 256
            or "\x00" in discussion_id
            or not isinstance(individual_note, bool)
        ):
            raise GitLabError("invalid_remote_data")
        raw_notes = _as_list(payload.get("notes"))
        notes = [
            self._normalize_note(_as_object(value))
            for value in raw_notes[:max_notes]
        ]
        return {
            "id": discussion_id,
            "individual_note": individual_note,
            "notes": notes,
            "note_count": len(raw_notes),
            "returned_note_count": len(notes),
            "notes_truncated": len(raw_notes) > max_notes,
        }

    def list_commit_comments(
        self,
        project: str | int,
        commit: str,
        *,
        max_items: int = 100,
        continuation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        commit = _validate_ref(_bounded_string(commit, _MAX_REF))
        max_items = _positive_bound(max_items, _MAX_COMMENTS)
        start_page, start_offset = _continuation_source(continuation)
        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)
        pages = self._paginate(
            f"/api/v4/projects/{resolved['id']}/repository/commits/{quote(commit, safe='')}/comments",
            params={},
            max_items=max_items,
            normalize=self._normalize_commit_comment,
            deadline=deadline,
            start_page=start_page,
            start_offset=start_offset,
        )
        return {
            "project": self._project_summary(resolved),
            "commit": commit,
            "comments": list(pages.items),
            "count": len(pages.items),
            "truncated": pages.truncated,
            "continuation": self._continuation(pages),
        }

    def list_commit_discussions(
        self,
        project: str | int,
        commit: str,
        *,
        max_discussions: int = 100,
        max_notes_per_discussion: int = 100,
        continuation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        commit = _validate_ref(_bounded_string(commit, _MAX_REF))
        max_discussions = _positive_bound(max_discussions, _MAX_DISCUSSIONS)
        max_notes_per_discussion = _positive_bound(
            max_notes_per_discussion, _MAX_NOTES_PER_DISCUSSION
        )
        start_page, start_offset = _continuation_source(continuation)
        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)
        pages = self._paginate(
            f"/api/v4/projects/{resolved['id']}/repository/commits/{quote(commit, safe='')}/discussions",
            params={},
            max_items=max_discussions,
            normalize=lambda item: self._normalize_discussion(
                item, max_notes=max_notes_per_discussion
            ),
            deadline=deadline,
            start_page=start_page,
            start_offset=start_offset,
        )
        discussions = list(pages.items)
        return {
            "project": self._project_summary(resolved),
            "commit": commit,
            "discussions": discussions,
            "count": len(discussions),
            "notes_truncated": any(
                discussion["notes_truncated"] for discussion in discussions
            ),
            "truncated": pages.truncated,
            "continuation": self._continuation(pages),
        }

    def _normalize_merge_request(
        self,
        payload: Mapping[str, Any],
        *,
        project_path: str,
    ) -> dict[str, Any]:
        identifier = _remote_positive_int(payload.get("id"))
        iid = _remote_positive_int(payload.get("iid"))
        title = payload.get("title")
        state = payload.get("state")
        draft = payload.get("draft")
        if (
            not isinstance(title, str)
            or not title
            or "\x00" in title
            or state not in {"opened", "closed", "merged", "locked"}
            or not isinstance(draft, bool)
        ):
            raise GitLabError("invalid_remote_data")
        try:
            if len(title.encode("utf-8")) > _MAX_MR_TITLE_INPUT:
                raise GitLabError("invalid_remote_data")
        except UnicodeEncodeError:
            raise GitLabError("invalid_remote_data") from None
        title = self._redact_text(title).encode("utf-8")[:_MAX_MR_TITLE].decode(
            "utf-8", errors="ignore"
        )
        source_branch = _validate_remote_ref(payload.get("source_branch"))
        target_branch = _validate_remote_ref(payload.get("target_branch"))
        _created, created_at = _rfc3339(payload.get("created_at"), remote=True)
        _updated, updated_at = _rfc3339(payload.get("updated_at"), remote=True)

        optional_times: dict[str, str | None] = {}
        for source, target in (("merged_at", "merged_at"), ("closed_at", "closed_at")):
            value = payload.get(source)
            if value is None:
                optional_times[target] = None
            else:
                _parsed, optional_times[target] = _rfc3339(value, remote=True)
        raw_labels = _as_list(payload.get("labels"))
        if len(raw_labels) > 100:
            raise GitLabError("invalid_remote_data")
        labels: list[str] = []
        for label in raw_labels:
            if (
                not isinstance(label, str)
                or not label
                or "\x00" in label
            ):
                raise GitLabError("invalid_remote_data")
            try:
                if len(label.encode("utf-8")) > 255:
                    raise GitLabError("invalid_remote_data")
            except UnicodeEncodeError:
                raise GitLabError("invalid_remote_data") from None
            labels.append(
                self._redact_text(label).encode("utf-8")[:255].decode(
                    "utf-8", errors="ignore"
                )
            )
        note_count = payload.get("user_notes_count")
        all_resolved = payload.get("blocking_discussions_resolved")
        if (
            isinstance(note_count, bool)
            or not isinstance(note_count, int)
            or note_count < 0
            or not isinstance(all_resolved, bool)
        ):
            raise GitLabError("invalid_remote_data")
        web_url = _canonical_remote_url(
            payload.get("web_url"),
            self.client.auth.origin,
            f"/{project_path}/-/merge_requests/{iid}",
        )
        return {
            "id": identifier,
            "iid": iid,
            "title": title,
            "state": state,
            "draft": draft,
            "source_branch": source_branch,
            "target_branch": target_branch,
            "author": self._normalize_user(payload.get("author")),
            "created_at": created_at,
            "updated_at": updated_at,
            **optional_times,
            "labels": labels,
            "note_count": note_count,
            "discussion_resolution": {"all_resolved": all_resolved},
            "web_url": web_url,
        }

    def _current_user(self, deadline: float) -> dict[str, Any]:
        user = _as_object(self.client.get_json("/api/v4/user", deadline=deadline))
        return {
            "id": _remote_positive_int(user.get("id")),
            "username": _gitlab_username(user.get("username"), remote=True),
        }

    def _global_merge_request_project(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        project_id = _remote_positive_int(payload.get("project_id"))
        iid = _remote_positive_int(payload.get("iid"))
        references = _as_object(payload.get("references"))
        full = _remote_text(references, "full", _MAX_PATH)
        suffix = f"!{iid}"
        if not full.endswith(suffix):
            raise GitLabError("invalid_remote_data")
        path = _namespace_path(full[: -len(suffix)], remote=True)
        if "/" not in path or full != f"{path}{suffix}":
            raise GitLabError("invalid_remote_data")
        web_url = _canonical_remote_url(
            payload.get("web_url"),
            self.client.auth.origin,
            f"/{path}/-/merge_requests/{iid}",
        )
        return {
            "id": project_id,
            "path": path,
            "web_url": f"{self.client.auth.origin}/{quote(path, safe='/')}",
        }

    def list_merge_requests(
        self,
        project: str | int | None = None,
        *,
        scope: str = "all",
        author: str | None = None,
        assignee: str | None = None,
        reviewer: str | None = None,
        state: str = "opened",
        source_branch: str | None = None,
        target_branch: str | None = None,
        search: str | None = None,
        order_by: str = "created_at",
        sort: str = "desc",
        created_after: str | None = None,
        updated_after: str | None = None,
        lookback_hours: int | None = None,
        max_items: int = 100,
        continuation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if project is not None:
            if isinstance(project, bool) or (
                isinstance(project, int) and project <= 0
            ):
                raise GitLabError("invalid_input")
            parsed_project = self._parse_project_reference(project)
            _project_endpoint(parsed_project["project"])
        scope = _bounded_enum(scope, _MR_SCOPES, 64)
        actors = {"author": author, "assignee": assignee, "reviewer": reviewer}
        for role, value in actors.items():
            if value is not None and value != "@me":
                actors[role] = _gitlab_username(value, remote=False)
        matching_actor = _MR_SCOPE_ACTORS.get(scope)
        if matching_actor is not None and actors[matching_actor] is not None:
            if actors[matching_actor] != "@me":
                raise GitLabError("invalid_input")
            actors[matching_actor] = None
        state = "opened" if state == "open" else state
        if not isinstance(state, str) or state not in {"opened", "closed", "merged", "all"}:
            raise GitLabError("invalid_input")
        if (
            not isinstance(order_by, str)
            or order_by not in {"created_at", "updated_at"}
            or not isinstance(sort, str)
            or sort not in {"asc", "desc"}
        ):
            raise GitLabError("invalid_input")
        if source_branch is not None:
            source_branch = _validate_ref(source_branch)
        if target_branch is not None:
            target_branch = _validate_ref(target_branch)
        if search is not None:
            search = _bounded_string(search, 512)
        if sum(
            value is not None for value in (lookback_hours, created_after, updated_after)
        ) > 1:
            raise GitLabError("invalid_input")
        if lookback_hours is not None:
            lookback_hours = _positive_bound(lookback_hours, 24 * 365)
            now = self._now()
            if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
                raise GitLabError("invalid_configuration")
            created_after = (
                now.astimezone(timezone.utc) - timedelta(hours=lookback_hours)
            ).isoformat().replace("+00:00", "Z")
        if created_after is not None:
            _created_after, created_after = _rfc3339(
                created_after, remote=False
            )
        if updated_after is not None:
            _updated_after, updated_after = _rfc3339(
                updated_after, remote=False
            )
        max_items = _positive_bound(max_items, _MAX_MERGE_REQUESTS)
        start_page, start_offset = _continuation_source(continuation)

        deadline = self.client.operation_deadline()
        params: dict[str, Any] = {
            "state": state,
            "scope": scope,
            "order_by": order_by,
            "sort": sort,
        }
        for key, value in (
            ("source_branch", source_branch),
            ("target_branch", target_branch),
            ("search", search),
            ("created_after", created_after),
            ("updated_after", updated_after),
        ):
            if value is not None:
                params[key] = value
        if any(value == "@me" for value in actors.values()):
            current_user = self._current_user(deadline)
            for role, value in actors.items():
                if value == "@me":
                    params[f"{role}_id"] = current_user["id"]
        for role, value in actors.items():
            if value is None or value == "@me":
                continue
            params["assignee_username[]" if role == "assignee" else f"{role}_username"] = (
                [value] if role == "assignee" else value
            )
        resolved = (
            self.resolve_project(project, deadline=deadline) if project is not None else None
        )
        known_projects: dict[int, dict[str, Any]] = {}

        def normalize(item: Mapping[str, Any]) -> dict[str, Any]:
            if resolved is not None:
                return self._normalize_merge_request(
                    item, project_path=resolved["path_with_namespace"]
                )
            item_project = self._global_merge_request_project(item)
            previous = known_projects.setdefault(item_project["id"], item_project)
            if previous != item_project:
                raise GitLabError("invalid_remote_data")
            return {
                **self._normalize_merge_request(item, project_path=item_project["path"]),
                "project": item_project,
            }
        pages = self._paginate(
            (
                f"/api/v4/projects/{resolved['id']}/merge_requests"
                if resolved is not None
                else "/api/v4/merge_requests"
            ),
            params=params,
            max_items=max_items,
            normalize=normalize,
            deadline=deadline,
            start_page=start_page,
            start_offset=start_offset,
        )
        activity_basis = (
            "created"
            if created_after is not None
            else "updated"
            if updated_after is not None
            else None
        )
        return {
            "project": self._project_summary(resolved) if resolved is not None else None,
            "filters": {
                "scope": scope,
                "author": actors["author"],
                "assignee": actors["assignee"],
                "reviewer": actors["reviewer"],
                "state": state,
                "source_branch": source_branch,
                "target_branch": target_branch,
                "search": search,
                "order_by": order_by,
                "sort": sort,
                "created_after": created_after,
                "updated_after": updated_after,
                "lookback_hours": lookback_hours,
            },
            "activity_basis": activity_basis,
            "merge_requests": list(pages.items),
            "count": len(pages.items),
            "truncated": pages.truncated,
            "continuation": self._continuation(pages),
        }

    def list_merge_request_commits(
        self,
        project: str | int,
        iid: int,
        *,
        max_items: int = 100,
        continuation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        iid = _positive_bound(iid, 2_147_483_647)
        max_items = _positive_bound(max_items, _MAX_COMMITS)
        start_page, start_offset = _continuation_source(continuation)
        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)
        pages = self._paginate(
            f"/api/v4/projects/{resolved['id']}/merge_requests/{iid}/commits",
            params={},
            max_items=max_items,
            normalize=lambda item: self._normalize_commit(
                item, project_path=resolved["path_with_namespace"]
            ),
            deadline=deadline,
            start_page=start_page,
            start_offset=start_offset,
        )
        return {
            "project": self._project_summary(resolved),
            "iid": iid,
            "commits": list(pages.items),
            "count": len(pages.items),
            "truncated": pages.truncated,
            "continuation": self._continuation(pages),
        }

    def list_merge_request_discussions(
        self,
        project: str | int,
        iid: int,
        *,
        max_discussions: int = 100,
        max_notes_per_discussion: int = 100,
        continuation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        iid = _positive_bound(iid, 2_147_483_647)
        max_discussions = _positive_bound(max_discussions, _MAX_DISCUSSIONS)
        max_notes_per_discussion = _positive_bound(
            max_notes_per_discussion, _MAX_NOTES_PER_DISCUSSION
        )
        start_page, start_offset = _continuation_source(continuation)
        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)
        pages = self._paginate(
            f"/api/v4/projects/{resolved['id']}/merge_requests/{iid}/discussions",
            params={},
            max_items=max_discussions,
            normalize=lambda item: self._normalize_discussion(
                item, max_notes=max_notes_per_discussion
            ),
            deadline=deadline,
            start_page=start_page,
            start_offset=start_offset,
        )
        discussions = list(pages.items)
        notes = [note for discussion in discussions for note in discussion["notes"]]
        resolvable_notes = [note for note in notes if note["resolvable"]]
        resolved_notes = [note for note in resolvable_notes if note["resolved"]]
        return {
            "project": self._project_summary(resolved),
            "iid": iid,
            "discussions": discussions,
            "count": len(discussions),
            "resolution_summary": {
                "resolvable_notes": len(resolvable_notes),
                "resolved_notes": len(resolved_notes),
                "unresolved_notes": len(resolvable_notes) - len(resolved_notes),
            },
            "summary_complete": not pages.truncated
            and not any(value["notes_truncated"] for value in discussions),
            "truncated": pages.truncated,
            "continuation": self._continuation(pages),
        }

    def create_mr_note(
        self,
        project: str | int,
        iid: int,
        body: str,
        *,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Post one top-level note on a merge request."""
        iid = self._iid(iid)
        body = self._note_body(body)
        try:
            execute = require_explicit_intent(
                dry_run=dry_run,
                confirm=confirm,
                action=f"merge request !{iid}",
            )
        except ConnectorError as exc:
            raise GitLabError(exc.category) from None
        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)
        project_path = resolved.get("path", resolved.get("path_with_namespace"))
        if not isinstance(project_path, str):
            raise GitLabError("invalid_remote_data")
        if not execute:
            return {
                "ok": True,
                "dry_run": True,
                "project": project_path,
                "iid": iid,
                "body": body,
                "note_id": None,
            }
        status, payload = self._write_json(
            "POST",
            f"/api/v4/projects/{resolved['id']}/merge_requests/{iid}/notes",
            {"body": body},
            deadline=deadline,
        )
        if status >= 400:
            raise self.client._error_for_status(status)

        def finish_note_write():
            if status != 201:
                raise GitLabError("invalid_remote_data")
            if not isinstance(payload, Mapping):
                raise GitLabError("invalid_remote_data")
            note_id = _remote_positive_int(payload.get("id"))
            return {
                "ok": True,
                "dry_run": False,
                "project": project_path,
                "iid": iid,
                "body": body,
                "note_id": note_id,
            }

        return self._usable_write_result(finish_note_write)

    def reply_to_discussion(
        self,
        project: str | int,
        iid: int,
        discussion_id: str,
        body: str,
        *,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Reply within an existing merge-request discussion thread."""
        iid = self._iid(iid)
        discussion_id = self._discussion_id(discussion_id)
        body = self._note_body(body)
        try:
            execute = require_explicit_intent(
                dry_run=dry_run,
                confirm=confirm,
                action=f"discussion {discussion_id} on !{iid}",
            )
        except ConnectorError as exc:
            raise GitLabError(exc.category) from None
        deadline = self.client.operation_deadline()
        project_info = self.resolve_project(project, deadline=deadline)
        project_path = project_info.get(
            "path", project_info.get("path_with_namespace")
        )
        if not isinstance(project_path, str):
            raise GitLabError("invalid_remote_data")
        base = (
            f"/api/v4/projects/{project_info['id']}/merge_requests/{iid}"
            f"/discussions/{discussion_id}"
        )
        if not execute:
            return {
                "ok": True,
                "dry_run": True,
                "project": project_path,
                "iid": iid,
                "discussion_id": discussion_id,
                "body": body,
                "note_id": None,
            }
        status, payload = self._write_json(
            "POST", f"{base}/notes", {"body": body}, deadline=deadline
        )
        if status >= 400:
            raise self.client._error_for_status(status)

        def finish_discussion_reply():
            if status != 201:
                raise GitLabError("invalid_remote_data")
            if not isinstance(payload, Mapping):
                raise GitLabError("invalid_remote_data")
            note_id = _remote_positive_int(payload.get("id"))
            return {
                "ok": True,
                "dry_run": False,
                "project": project_path,
                "iid": iid,
                "discussion_id": discussion_id,
                "body": body,
                "note_id": note_id,
            }

        return self._usable_write_result(finish_discussion_reply)

    def resolve_discussion(
        self,
        project: str | int,
        iid: int,
        discussion_id: str,
        *,
        resolved: bool = True,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Mark a discussion resolved, or reopen it.

        This remains separate from ``reply_to_discussion``: marking a thread
        settled is a judgement and must never be a side effect of replying.
        """
        iid = self._iid(iid)
        discussion_id = self._discussion_id(discussion_id)
        if type(resolved) is not bool:
            raise GitLabError("invalid_input")
        try:
            execute = require_explicit_intent(
                dry_run=dry_run,
                confirm=confirm,
                action=f"discussion {discussion_id} on !{iid}",
            )
        except ConnectorError as exc:
            raise GitLabError(exc.category) from None
        deadline = self.client.operation_deadline()
        project_info = self.resolve_project(project, deadline=deadline)
        project_path = project_info.get(
            "path", project_info.get("path_with_namespace")
        )
        if not isinstance(project_path, str):
            raise GitLabError("invalid_remote_data")
        if not execute:
            return {
                "ok": True,
                "dry_run": True,
                "project": project_path,
                "iid": iid,
                "discussion_id": discussion_id,
                "resolved": resolved,
            }
        status, payload = self._write_json(
            "PUT",
            f"/api/v4/projects/{project_info['id']}/merge_requests/{iid}"
            f"/discussions/{discussion_id}",
            {"resolved": resolved},
            deadline=deadline,
        )
        if status >= 400:
            raise self.client._error_for_status(status)

        def finish_discussion_resolution():
            if status != 200 or not isinstance(payload, Mapping):
                raise GitLabError("invalid_remote_data")
            notes = payload.get("notes")
            if (
                payload.get("id") != discussion_id
                or not isinstance(notes, list)
                or not 1 <= len(notes) <= _MAX_NOTES_PER_DISCUSSION
            ):
                raise GitLabError("invalid_remote_data")
            resolvable_states = []
            for raw_note in notes:
                if not isinstance(raw_note, Mapping):
                    raise GitLabError("invalid_remote_data")
                resolvable = raw_note.get("resolvable")
                note_resolved = raw_note.get("resolved")
                if type(resolvable) is not bool or type(note_resolved) is not bool:
                    raise GitLabError("invalid_remote_data")
                if resolvable:
                    resolvable_states.append(note_resolved)
            if not resolvable_states or any(
                state is not resolved for state in resolvable_states
            ):
                raise GitLabError("invalid_remote_data")
            return {
                "ok": True,
                "dry_run": False,
                "project": project_path,
                "iid": iid,
                "discussion_id": discussion_id,
                "resolved": resolved,
            }

        return self._usable_write_result(finish_discussion_resolution)

    def merge_request_approvals(
        self, project: str | int, iid: int
    ) -> dict[str, Any]:
        """Read an MR's approval state and the users who have approved it."""
        iid = self._iid(iid)
        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)
        project_path = resolved.get("path", resolved.get("path_with_namespace"))
        if not isinstance(project_path, str):
            raise GitLabError("invalid_remote_data")
        payload = self.client.get_json(
            f"/api/v4/projects/{resolved['id']}/merge_requests/{iid}/approvals",
            deadline=deadline,
        )
        if not isinstance(payload, Mapping):
            raise GitLabError("invalid_remote_data")
        approved = payload.get("approved")
        approvals_required = payload.get("approvals_required")
        approvals_left = payload.get("approvals_left")
        if (
            type(approved) is not bool
            or type(approvals_required) is not int
            or approvals_required < 0
            or type(approvals_left) is not int
            or approvals_left < 0
        ):
            raise GitLabError("invalid_remote_data")
        raw_approved_by = _as_list(payload.get("approved_by"))
        approvers = []
        for raw_entry in raw_approved_by:
            entry = _as_object(raw_entry)
            user = _as_object(entry.get("user"))
            username = user.get("username")
            if (
                not isinstance(username, str)
                or not username
                or username != username.strip()
                or len(username) > 255
                or "\x00" in username
            ):
                raise GitLabError("invalid_remote_data")
            approvers.append(self._redact_text(username))
        return {
            "project": project_path,
            "iid": iid,
            "approved": approved,
            "approvals_required": approvals_required,
            "approvals_left": approvals_left,
            "approved_by": approvers[:100],
            "approved_by_truncated": len(approvers) > 100,
        }

    def approve_merge_request(
        self,
        project: str | int,
        iid: int,
        *,
        sha: str | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Approve one merge request, optionally pinning it to a reviewed SHA."""
        iid = self._iid(iid)
        body: dict[str, Any] = {}
        if sha is not None:
            body["sha"] = self._sha(sha)
        try:
            execute = require_explicit_intent(
                dry_run=dry_run, confirm=confirm, action=f"merge request !{iid}"
            )
        except ConnectorError as exc:
            raise GitLabError(exc.category) from None
        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)
        project_path = resolved.get("path", resolved.get("path_with_namespace"))
        if not isinstance(project_path, str):
            raise GitLabError("invalid_remote_data")
        if not execute:
            return {
                "ok": True,
                "dry_run": True,
                "project": project_path,
                "iid": iid,
                "sha": sha,
            }
        status, payload = self._write_json(
            "POST",
            f"/api/v4/projects/{resolved['id']}/merge_requests/{iid}/approve",
            body,
            deadline=deadline,
        )
        if status >= 400:
            raise self.client._error_for_status(status)

        def finish_approval_write():
            if status != 201 or not isinstance(payload, Mapping):
                raise GitLabError("invalid_remote_data")
            approved = payload.get("approved")
            if type(approved) is not bool:
                raise GitLabError("invalid_remote_data")
            return {
                "ok": True,
                "dry_run": False,
                "project": project_path,
                "iid": iid,
                "sha": sha,
                "approved": approved,
            }

        return self._usable_write_result(finish_approval_write)

    def merge_merge_request(
        self,
        project: str | int,
        iid: int,
        *,
        sha: str | None = None,
        squash: bool | None = None,
        remove_source_branch: bool | None = None,
        merge_when_pipeline_succeeds: bool = False,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Merge one merge request without retrying or reconciling it.

        Optional flags remain absent when the caller leaves them unset so
        this operation preserves the project's configured defaults.
        """
        iid = self._iid(iid)
        body: dict[str, Any] = {}
        if sha is not None:
            body["sha"] = self._sha(sha)
        for key, value in (
            ("squash", squash),
            ("should_remove_source_branch", remove_source_branch),
        ):
            if value is not None:
                if type(value) is not bool:
                    raise GitLabError("invalid_input")
                body[key] = value
        if type(merge_when_pipeline_succeeds) is not bool:
            raise GitLabError("invalid_input")
        if merge_when_pipeline_succeeds:
            body["merge_when_pipeline_succeeds"] = True
        try:
            execute = require_explicit_intent(
                dry_run=dry_run,
                confirm=confirm,
                action=f"merge request !{iid}",
            )
        except ConnectorError as exc:
            raise GitLabError(exc.category) from None

        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)
        project_path = resolved.get("path", resolved.get("path_with_namespace"))
        if not isinstance(project_path, str):
            raise GitLabError("invalid_remote_data")
        if not execute:
            return {
                "ok": True,
                "dry_run": True,
                "project": project_path,
                "iid": iid,
                "requested": body,
            }

        status, payload = self._write_json(
            "PUT",
            f"/api/v4/projects/{resolved['id']}/merge_requests/{iid}/merge",
            body,
            deadline=deadline,
            passthrough_error_statuses=frozenset({400, 405, 409}),
        )
        if status in {405, 409}:
            raise GitLabError("conflict")
        if status >= 400:
            raise self.client._error_for_status(status)

        def finish_merge_write():
            if status != 200 or not isinstance(payload, Mapping):
                raise GitLabError("invalid_remote_data")
            state = payload.get("state")
            if (
                type(state) is not str
                or not state
                or len(state) > 64
                or state != state.strip()
                or "\x00" in state
            ):
                raise GitLabError("invalid_remote_data")
            expected_states = (
                {"opened", "merged"}
                if merge_when_pipeline_succeeds
                else {"merged"}
            )
            if state not in expected_states:
                raise GitLabError("invalid_remote_data")
            merge_commit_sha = payload.get("merge_commit_sha")
            if merge_commit_sha is not None and (
                type(merge_commit_sha) is not str
                or re.fullmatch(r"[0-9a-f]{40}", merge_commit_sha) is None
            ):
                raise GitLabError("invalid_remote_data")
            return {
                "ok": True,
                "dry_run": False,
                "project": project_path,
                "iid": iid,
                "state": state,
                "merge_commit_sha": merge_commit_sha,
            }

        return self._usable_write_result(finish_merge_write)

    def update_merge_request(
        self,
        project: str | int,
        iid: int,
        *,
        title: str | None = None,
        description: str | None = None,
        add_labels: list | None = None,
        remove_labels: list | None = None,
        state_event: str | None = None,
        draft: bool | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Update an existing merge request with one non-retried write.

        Label changes are incremental so this operation never replaces a
        concurrently edited label list. Draft changes require the title in
        the same call because GitLab represents draft state in that title.
        """
        iid = self._iid(iid)
        body: dict[str, Any] = {}
        if title is not None:
            if (
                type(title) is not str
                or not title.strip()
                or len(title) > _MAX_MR_TITLE_INPUT
                or "\x00" in title
            ):
                raise GitLabError("invalid_input")
            body["title"] = title
        if description is not None:
            if (
                type(description) is not str
                or len(description) > _MAX_MR_DESCRIPTION
                or "\x00" in description
            ):
                raise GitLabError("invalid_input")
            body["description"] = _reject_hidden_quick_actions(description)

        normalized_labels: dict[str, list[str]] = {}
        for key, labels in (
            ("add_labels", add_labels),
            ("remove_labels", remove_labels),
        ):
            if labels is None:
                continue
            if (
                type(labels) is not list
                or not labels
                or len(labels) > 50
                or any(
                    type(label) is not str
                    or not label
                    or label != label.strip()
                    or len(label) > 255
                    or "," in label
                    or "\x00" in label
                    for label in labels
                )
            ):
                raise GitLabError("invalid_input")
            normalized_labels[key] = labels
            body[key] = ",".join(labels)
        if set(normalized_labels.get("add_labels", ())) & set(
            normalized_labels.get("remove_labels", ())
        ):
            raise GitLabError("invalid_input")

        if state_event is not None:
            if type(state_event) is not str or state_event not in _MR_STATE_EVENTS:
                raise GitLabError("invalid_input")
            body["state_event"] = state_event
        if draft is not None:
            if type(draft) is not bool or "title" not in body:
                raise GitLabError("invalid_input")
            base_title = body["title"]
            marker = _MR_DRAFT_PREFIX.match(base_title)
            while marker is not None:
                base_title = base_title[marker.end() :].strip()
                marker = _MR_DRAFT_PREFIX.match(base_title)
            if not base_title:
                raise GitLabError("invalid_input")
            body["title"] = f"Draft: {base_title}" if draft else base_title
        if "title" in body and len(body["title"]) > _MAX_MR_TITLE_INPUT:
            raise GitLabError("invalid_input")
        if not body:
            raise GitLabError("invalid_input")

        try:
            execute = require_explicit_intent(
                dry_run=dry_run,
                confirm=confirm,
                action=f"merge request !{iid}",
            )
        except ConnectorError as exc:
            raise GitLabError(exc.category) from None

        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)
        project_path = resolved.get("path", resolved.get("path_with_namespace"))
        if not isinstance(project_path, str):
            raise GitLabError("invalid_remote_data")
        if not execute:
            return {
                "ok": True,
                "dry_run": True,
                "project": project_path,
                "iid": iid,
                "requested": body,
            }

        status, payload = self._write_json(
            "PUT",
            f"/api/v4/projects/{resolved['id']}/merge_requests/{iid}",
            body,
            deadline=deadline,
        )
        if status >= 400:
            raise self.client._error_for_status(status)

        def finish_update_write():
            if status != 200 or not isinstance(payload, Mapping):
                raise GitLabError("invalid_remote_data")
            if type(payload.get("iid")) is not int or payload["iid"] != iid:
                raise GitLabError("invalid_remote_data")
            remote_state = payload.get("state")
            if (
                type(remote_state) is not str
                or not remote_state
                or len(remote_state) > 64
                or remote_state != remote_state.strip()
                or "\x00" in remote_state
                or remote_state not in _MR_RESPONSE_STATES
            ):
                raise GitLabError("invalid_remote_data")
            if draft is not None and (
                type(payload.get("draft")) is not bool
                or payload["draft"] is not draft
            ):
                raise GitLabError("invalid_remote_data")
            for field in ("title", "description"):
                if field in body and (
                    type(payload.get(field)) is not str
                    or payload[field] != body[field]
                ):
                    raise GitLabError("invalid_remote_data")
            if normalized_labels:
                remote_labels = payload.get("labels")
                if (
                    type(remote_labels) is not list
                    or len(remote_labels) > _MAX_MR_LABELS
                    or any(
                        type(label) is not str
                        or not label
                        or len(label) > 255
                        or "\x00" in label
                        for label in remote_labels
                    )
                ):
                    raise GitLabError("invalid_remote_data")
                if any(
                    label not in remote_labels
                    for label in normalized_labels.get("add_labels", ())
                ) or any(
                    label in remote_labels
                    for label in normalized_labels.get("remove_labels", ())
                ):
                    raise GitLabError("invalid_remote_data")
            expected_state = {"close": "closed", "reopen": "opened"}.get(
                state_event
            )
            if expected_state is not None and remote_state != expected_state:
                raise GitLabError("invalid_remote_data")
            return {
                "ok": True,
                "dry_run": False,
                "project": project_path,
                "iid": iid,
                "state": remote_state,
                "requested": body,
            }

        return self._usable_write_result(finish_update_write)

    def _list_named_refs(
        self, project_id: int, kind: str, *, deadline: float
    ) -> list[str]:
        refs: list[str] = []
        page = 1
        while page <= self.client.max_ref_pages:
            payload, headers = self.client.get_json_page(
                f"/api/v4/projects/{project_id}/repository/{kind}",
                params={"per_page": 100, "page": page},
                deadline=deadline,
            )
            values = _as_list(payload)
            for value in values:
                item = _as_object(value)
                name = item.get("name")
                if not isinstance(name, str) or not name or len(name) > _MAX_REF:
                    raise GitLabError("invalid_remote_data")
                refs.append(name)
                if len(refs) >= 2000:
                    raise GitLabError("capacity")
            next_header = str(headers.get("x-next-page", "")).strip()
            if not next_header and len(values) < 100:
                break
            if next_header:
                try:
                    next_page = int(next_header)
                except ValueError:
                    raise GitLabError("invalid_remote_data") from None
                if next_page <= page:
                    raise GitLabError("invalid_remote_data")
                candidate = next_page
            else:
                candidate = page + 1
            if candidate > self.client.max_ref_pages:
                raise GitLabError("capacity")
            page = candidate
        return refs

    def _resolve_link_ref(
        self, project_id: int, suffix: str, *, deadline: float
    ) -> tuple[str, str]:
        suffix = _bounded_string(suffix, _MAX_PATH + _MAX_REF)
        refs = self._list_named_refs(project_id, "branches", deadline=deadline)
        refs.extend(self._list_named_refs(project_id, "tags", deadline=deadline))
        matches = [
            name for name in refs if suffix == name or suffix.startswith(name + "/")
        ]
        if matches:
            selected = max(matches, key=lambda item: (len(item), item))
        else:
            selected = suffix.split("/", 1)[0]
            selected = _validate_ref(selected)
            encoded = quote(selected, safe="")
            self.client.get_json(
                f"/api/v4/projects/{project_id}/repository/commits/{encoded}",
                deadline=deadline,
            )
        remainder = suffix[len(selected) :].lstrip("/")
        return selected, _validate_path(remainder, allow_empty=True)

    def _paginate(
        self,
        path: str,
        *,
        params: Mapping[str, Any],
        max_items: int,
        normalize: Callable[[Mapping[str, Any]], dict[str, Any]],
        deadline: float | None = None,
        start_page: int = 1,
        start_offset: int = 0,
    ) -> PageResult:
        if deadline is None:
            deadline = self.client.operation_deadline()
        if (
            isinstance(start_page, bool)
            or not isinstance(start_page, int)
            or start_page <= 0
            or isinstance(start_offset, bool)
            or not isinstance(start_offset, int)
            or not 0 <= start_offset < 100
        ):
            raise GitLabError("invalid_input")
        page = start_page
        pages_fetched = 0
        first_page = True
        items: list[dict[str, Any]] = []
        truncated = False
        next_page: int | None = None
        next_offset: int | None = None
        while pages_fetched < self.client.max_pages:
            query = dict(params)
            query.update({"per_page": 100, "page": page})
            payload, headers = self.client.get_json_page(
                path, params=query, deadline=deadline
            )
            pages_fetched += 1
            values = _as_list(payload)
            offset_base = start_offset if first_page else 0
            if offset_base > len(values):
                raise GitLabError("invalid_input")
            for offset, raw in enumerate(values[offset_base:], start=offset_base):
                if len(items) >= max_items:
                    truncated = True
                    next_page = page
                    next_offset = offset
                    break
                items.append(normalize(_as_object(raw)))
            if truncated:
                break
            first_page = False
            next_header = str(headers.get("x-next-page", "")).strip()
            if not next_header and len(values) < 100:
                break
            candidate = page + 1
            if next_header:
                try:
                    candidate = int(next_header)
                except ValueError:
                    raise GitLabError("invalid_remote_data") from None
                if candidate <= page:
                    raise GitLabError("invalid_remote_data")
            if pages_fetched >= self.client.max_pages:
                truncated = True
                next_page = candidate
                break
            page = candidate
        return PageResult(tuple(items), truncated, next_page, next_offset)

    @staticmethod
    def _continuation(pages: PageResult) -> dict[str, int] | None:
        if pages.next_page is None:
            return None
        if pages.next_offset is not None:
            return {"page": pages.next_page, "offset": pages.next_offset}
        return {"next_page": pages.next_page}

    def list_repository_tree(
        self,
        project: str | int,
        *,
        ref: str,
        path: str = "",
        recursive: bool = False,
        max_items: int = 200,
    ) -> dict[str, Any]:
        project_endpoint = _project_endpoint(project)
        ref = _validate_ref(ref)
        path = _validate_path(path, allow_empty=True)
        max_items = _positive_bound(max_items, _MAX_TREE_ITEMS)
        if not isinstance(recursive, bool):
            raise GitLabError("invalid_input")

        def normalize(item: Mapping[str, Any]) -> dict[str, Any]:
            output = {}
            for field in ("id", "name", "path", "type", "mode"):
                value = item.get(field)
                if not isinstance(value, str) or len(value) > _MAX_PATH:
                    raise GitLabError("invalid_remote_data")
                output[field] = value
            if output["type"] not in {"blob", "tree", "commit"}:
                raise GitLabError("invalid_remote_data")
            return output

        pages = self._paginate(
            f"/api/v4/projects/{project_endpoint}/repository/tree",
            params={"ref": ref, "path": path, "recursive": str(recursive).lower()},
            max_items=max_items,
            normalize=normalize,
        )
        entries = sorted(pages.items, key=lambda item: (item["path"], item["id"]))
        return {
            "project": str(project),
            "ref": ref,
            "path": path,
            "recursive": recursive,
            "entries": entries,
            "count": len(entries),
            "truncated": pages.truncated,
            "continuation": self._continuation(pages),
        }

    def read_file(
        self,
        project: str | int,
        file_path: str,
        *,
        ref: str,
        max_bytes: int = 100 * 1024,
    ) -> dict[str, Any]:
        project_endpoint = _project_endpoint(project)
        file_path = _validate_path(file_path, allow_empty=False)
        ref = _validate_ref(ref)
        max_bytes = _positive_bound(max_bytes, _MAX_FILE_BYTES)
        encoded_path = quote(file_path, safe="")
        payload = _as_object(
            self.client.get_json(
                f"/api/v4/projects/{project_endpoint}/repository/files/{encoded_path}",
                params={"ref": ref},
            )
        )
        if payload.get("encoding") != "base64" or not isinstance(
            payload.get("content"), str
        ):
            raise GitLabError("invalid_remote_data")
        declared_size = payload.get("size")
        if (
            isinstance(declared_size, bool)
            or not isinstance(declared_size, int)
            or declared_size < 0
        ):
            raise GitLabError("invalid_remote_data")
        if declared_size > max_bytes:
            raise GitLabError("capacity")
        compact = "".join(payload["content"].split())
        try:
            decoded = base64.b64decode(compact, validate=True)
        except (binascii.Error, ValueError):
            raise GitLabError("invalid_remote_data") from None
        if len(decoded) > max_bytes:
            raise GitLabError("capacity")
        output: dict[str, Any] = {
            "project": str(project),
            "ref": ref,
            "path": file_path,
            "size": len(decoded),
            "blob_id": payload.get("blob_id")
            if isinstance(payload.get("blob_id"), str)
            else None,
        }
        diagnostic = None
        if b"\x00" in decoded:
            diagnostic = "binary"
        else:
            try:
                text = decoded.decode("utf-8")
            except UnicodeDecodeError:
                diagnostic = "undecodable"
        if diagnostic:
            output.update({"kind": "binary", "diagnostic": diagnostic})
        else:
            output.update({"kind": "text", "content": text})
        return output

    def read_merge_request(self, project: str | int, iid: int) -> dict[str, Any]:
        project_endpoint = _project_endpoint(project)
        iid = _positive_bound(iid, 2_147_483_647)
        payload = _as_object(
            self.client.get_json(
                f"/api/v4/projects/{project_endpoint}/merge_requests/{iid}/changes"
            )
        )
        head_sha = payload.get("sha")
        if (
            type(head_sha) is not str
            or re.fullmatch(r"[0-9a-f]{40}", head_sha) is None
        ):
            raise GitLabError("invalid_remote_data")
        raw_changes = _as_list(payload.get("changes"))
        changes: list[dict[str, Any]] = []
        remaining = self.client.max_diff_bytes
        local_truncated = len(raw_changes) > self.client.max_changes
        overflow = payload.get("overflow")
        if overflow is not None and not isinstance(overflow, bool):
            raise GitLabError("invalid_remote_data")
        remote_truncated = overflow is True
        changes_count = payload.get("changes_count")
        if changes_count is not None:
            if isinstance(changes_count, bool):
                raise GitLabError("invalid_remote_data")
            capped_count = False
            if isinstance(changes_count, str):
                capped_count = changes_count.endswith("+")
                digits = changes_count[:-1] if capped_count else changes_count
                if not digits.isdigit() or len(digits) > 10:
                    raise GitLabError("invalid_remote_data")
                changes_count = int(digits)
            if not isinstance(changes_count, int) or changes_count < len(raw_changes):
                raise GitLabError("invalid_remote_data")
            remote_truncated = (
                remote_truncated or capped_count or changes_count > len(raw_changes)
            )
        for raw in raw_changes[: self.client.max_changes]:
            item = _as_object(raw)
            diff = item.get("diff")
            if not isinstance(diff, str):
                raise GitLabError("invalid_remote_data")
            encoded = diff.encode("utf-8")
            if len(encoded) > remaining:
                diff = encoded[:remaining].decode("utf-8", errors="ignore")
                local_truncated = True
            remaining -= len(diff.encode("utf-8"))
            projected = {"diff": diff}
            for field in ("old_path", "new_path"):
                value = item.get(field)
                if not isinstance(value, str) or len(value) > _MAX_PATH:
                    raise GitLabError("invalid_remote_data")
                projected[field] = value
            for field in ("new_file", "renamed_file", "deleted_file"):
                projected[field] = item.get(field) is True
            changes.append(projected)
            if remaining <= 0:
                local_truncated = local_truncated or len(changes) < len(raw_changes)
                break
        truncated = local_truncated or remote_truncated
        warnings = []
        if local_truncated:
            warnings.append("merge_request_changes_truncated")
        if remote_truncated:
            warnings.append("merge_request_remote_truncated")
        result = {
            "id": payload.get("id"),
            "iid": payload.get("iid"),
            "head_sha": head_sha,
            "title": payload.get("title"),
            "state": payload.get("state"),
            "source_branch": payload.get("source_branch"),
            "target_branch": payload.get("target_branch"),
            "web_url": payload.get("web_url"),
            "changes": changes,
            "change_count": len(changes),
            "truncated": truncated,
            "warnings": warnings,
        }
        for field in ("id", "iid"):
            value = result[field]
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise GitLabError("invalid_remote_data")
        if result["iid"] != iid:
            raise GitLabError("invalid_remote_data")
        for field in ("title", "state", "source_branch", "target_branch", "web_url"):
            if (
                not isinstance(result[field], str)
                or len(result[field]) > _MAX_PROJECT_REFERENCE
            ):
                raise GitLabError("invalid_remote_data")
        _same_origin_url(result["web_url"], self.client.auth.origin)
        return result

    def _normalize_pipeline_summary(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        identifier = _remote_positive_int(payload.get("id"))
        projected: dict[str, Any] = {"id": identifier}
        iid = payload.get("iid")
        if iid is not None:
            projected["iid"] = _remote_positive_int(iid)
        ref = payload.get("ref")
        projected["ref"] = None if ref is None else _validate_remote_ref(ref)
        sha = payload.get("sha")
        projected["sha"] = None if sha is None else _commit_sha(sha)
        for field in ("status", "source"):
            value = payload.get(field)
            if value is not None and (
                not isinstance(value, str)
                or not value
                or len(value) > _MAX_PROJECT_REFERENCE
                or "\x00" in value
            ):
                raise GitLabError("invalid_remote_data")
            projected[field] = value
        for field in ("created_at", "updated_at"):
            value = payload.get(field)
            if value is None:
                projected[field] = None
            else:
                _parsed, projected[field] = _rfc3339(value, remote=True)
        web_url = payload.get("web_url")
        projected["web_url"] = (
            None if web_url is None else _same_origin_url(web_url, self.client.auth.origin)
        )
        return projected

    def list_pipelines(
        self,
        project: str | int,
        *,
        ref: str | None = None,
        status: str | None = None,
        max_items: int = 50,
    ) -> dict[str, Any]:
        project_endpoint = _project_endpoint(project)
        max_items = _positive_bound(max_items, _MAX_PIPELINES)
        params: dict[str, Any] = {"order_by": "id", "sort": "desc"}
        if ref is not None:
            params["ref"] = _validate_ref(ref)
        if status is not None:
            params["status"] = _bounded_string(status, 64)

        pages = self._paginate(
            f"/api/v4/projects/{project_endpoint}/pipelines",
            params=params,
            max_items=max_items,
            normalize=self._normalize_pipeline_summary,
        )
        return {
            "project": str(project),
            "pipelines": list(pages.items),
            "count": len(pages.items),
            "truncated": pages.truncated,
            "continuation": self._continuation(pages),
        }

    def list_merge_request_pipelines(
        self,
        project: str | int,
        iid: int,
        *,
        max_items: int = 100,
        continuation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        iid = _positive_bound(iid, 2_147_483_647)
        max_items = _positive_bound(max_items, _MAX_PIPELINES)
        start_page, start_offset = _continuation_source(continuation)
        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)
        pages = self._paginate(
            f"/api/v4/projects/{resolved['id']}/merge_requests/{iid}/pipelines",
            params={},
            max_items=max_items,
            normalize=self._normalize_pipeline_summary,
            deadline=deadline,
            start_page=start_page,
            start_offset=start_offset,
        )
        return {
            "project": {"id": resolved["id"], "path": resolved["path_with_namespace"]},
            "merge_request": {"iid": iid},
            "pipelines": list(pages.items),
            "count": len(pages.items),
            "truncated": pages.truncated,
            "continuation": self._continuation(pages),
        }

    def read_pipeline(
        self, project: str | int, pipeline_id: int
    ) -> dict[str, Any]:
        pipeline_id = _positive_bound(pipeline_id, 2_147_483_647)
        deadline = self.client.operation_deadline()
        project_result = self.resolve_project(project, deadline=deadline)
        endpoint = _project_endpoint(project_result["id"])
        payload = _as_object(
            self.client.get_json(
                f"/api/v4/projects/{endpoint}/pipelines/{pipeline_id}",
                deadline=deadline,
            )
        )
        if _remote_positive_int(payload.get("id")) != pipeline_id:
            raise GitLabError("invalid_remote_data")
        if "project_id" in payload and (
            _remote_positive_int(payload.get("project_id"))
            != project_result["id"]
        ):
            raise GitLabError("invalid_remote_data")

        def required_string(field: str, maximum: int) -> str:
            value = payload.get(field)
            if (
                field not in payload
                or not isinstance(value, str)
                or not value
                or len(value) > maximum
                or "\x00" in value
            ):
                raise GitLabError("invalid_remote_data")
            return value

        result: dict[str, Any] = {
            "project": {
                "id": project_result["id"],
                "path": project_result["path_with_namespace"],
            },
            "pipeline_id": pipeline_id,
            "status": required_string("status", 64),
            "ref": required_string("ref", _MAX_REF),
            "sha": required_string("sha", 128),
            "source": required_string("source", 128),
            "web_url": required_string("web_url", _MAX_PROJECT_REFERENCE),
        }
        _same_origin_url(result["web_url"], self.client.auth.origin)
        for field in ("created_at", "updated_at", "started_at", "finished_at"):
            if field not in payload:
                raise GitLabError("invalid_remote_data")
            value = payload[field]
            if value is None:
                result[field] = None
            else:
                result[field] = _rfc3339(value, remote=True)[1]
        return result

    def _write_json(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any],
        *,
        deadline: float,
        passthrough_error_statuses: frozenset[int] = frozenset({400, 409}),
    ) -> tuple[int, Any]:
        """Perform exactly one bounded mutating request without retrying."""

        status, decoded, _headers = self.client.request_json_response(
            method,
            path,
            json_body=dict(payload),
            deadline=deadline,
            raise_on_status=False,
        )
        if status >= 400 and status not in passthrough_error_statuses:
            raise self.client._error_for_status(status)
        return status, decoded

    @staticmethod
    def _usable_write_result(operation):
        """Convert unusable post-dispatch success evidence to ambiguity."""

        unusable = False
        try:
            return operation()
        except GitLabError:
            unusable = True
        if unusable:
            raise GitLabError("write_ambiguous") from None

    def _write_project(self, project: str | int, *, deadline: float) -> dict[str, Any]:
        return self._ci_project(project, deadline=deadline)

    @staticmethod
    def _require_reconciled_identity(
        partial: Mapping[str, Any], reconciled: Mapping[str, Any]
    ) -> None:
        if any(
            field not in reconciled
            or type(reconciled[field]) is not type(value)
            or reconciled[field] != value
            for field, value in partial.items()
        ):
            raise GitLabError("invalid_remote_data")

    def _branch_result(
        self,
        payload: Any,
        *,
        project: str | int,
        project_result: Mapping[str, Any],
        branch: str,
        source_ref: str,
        created: bool,
    ) -> dict[str, Any]:
        item = _as_object(payload)
        name = item.get("name")
        commit = item.get("commit")
        web_url = item.get("web_url")
        if name != branch or not isinstance(commit, Mapping):
            raise GitLabError("invalid_remote_data")
        commit_id = _validate_remote_ref(commit.get("id"))
        commit_short_id = commit.get("short_id")
        if "short_id" in commit and (
            not isinstance(commit_short_id, str)
            or not commit_short_id
            or len(commit_short_id) > 64
            or not commit_id.startswith(commit_short_id)
        ):
            raise GitLabError("invalid_remote_data")
        if "project_id" in item:
            remote_project_id = _remote_positive_int(item.get("project_id"))
            if remote_project_id != project_result["id"]:
                raise GitLabError("invalid_remote_data")
        project_path = project_result["path_with_namespace"]
        branch_path = f"/{project_path}/-/tree/{branch}"
        web_url = _canonical_remote_url(web_url, self.client.auth.origin, branch_path)
        if "web_url" in commit:
            _canonical_remote_url(
                commit.get("web_url"),
                self.client.auth.origin,
                f"/{project_path}/-/commit/{commit_id}",
            )
        return {
            "project": str(project),
            "branch": branch,
            "source_ref": source_ref,
            "commit_id": commit_id,
            "web_url": web_url,
            "created": created,
            "reused": not created,
            "dry_run": False,
        }

    def _validate_partial_branch_identity(
        self,
        payload: Mapping[str, Any],
        *,
        project_result: Mapping[str, Any],
        branch: str,
    ) -> dict[str, Any]:
        identity: dict[str, Any] = {}
        if "name" in payload and payload.get("name") != branch:
            raise GitLabError("invalid_remote_data")
        if "name" in payload:
            identity["name"] = branch
        if "project_id" in payload:
            remote_project_id = _remote_positive_int(payload.get("project_id"))
            if remote_project_id != project_result["id"]:
                raise GitLabError("invalid_remote_data")
            identity["project_id"] = remote_project_id
        project_path = project_result["path_with_namespace"]
        if "web_url" in payload:
            identity["web_url"] = _canonical_remote_url(
                payload.get("web_url"),
                self.client.auth.origin,
                f"/{project_path}/-/tree/{branch}",
            )
        if "commit" in payload:
            commit = _as_object(payload.get("commit"))
            commit_id = _validate_remote_ref(commit.get("id"))
            identity["commit_id"] = commit_id
            commit_short_id = commit.get("short_id")
            if "short_id" in commit and (
                not isinstance(commit_short_id, str)
                or not commit_short_id
                or len(commit_short_id) > 64
                or not commit_id.startswith(commit_short_id)
            ):
                raise GitLabError("invalid_remote_data")
            if "short_id" in commit:
                identity["commit_short_id"] = commit_short_id
            if "web_url" in commit:
                identity["commit_web_url"] = _canonical_remote_url(
                    commit.get("web_url"),
                    self.client.auth.origin,
                    f"/{project_path}/-/commit/{commit_id}",
                )
        return identity

    def create_branch(
        self,
        project: str | int,
        *,
        ticket_key: str,
        summary: str,
        prefix: str = "fix",
        source_ref: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        branch = _build_branch_name(prefix, ticket_key, summary)
        if source_ref is not None:
            source_ref = _validate_ref(source_ref)
        if not isinstance(dry_run, bool):
            raise GitLabError("invalid_input")
        deadline = self.client.operation_deadline()
        project_result = self._write_project(project, deadline=deadline)
        selected_source = source_ref or project_result["default_branch"]
        if dry_run:
            return {
                "project": str(project),
                "branch": branch,
                "source_ref": selected_source,
                "created": False,
                "reused": False,
                "dry_run": True,
            }
        endpoint = _project_endpoint(project_result["id"])
        branch_path = (
            f"/api/v4/projects/{endpoint}/repository/branches/{quote(branch, safe='')}"
        )
        try:
            existing = self.client.get_json(branch_path, deadline=deadline)
        except GitLabError as exc:
            if exc.category != "not_found":
                raise
        else:
            return self._branch_result(
                existing,
                project=project,
                project_result=project_result,
                branch=branch,
                source_ref=selected_source,
                created=False,
            )
        status, created_payload = self._write_json(
            "POST",
            f"/api/v4/projects/{endpoint}/repository/branches",
            {"branch": branch, "ref": selected_source},
            deadline=deadline,
        )
        if (
            status in {400, 409}
            and "branch already exists"
            in " ".join(self._remote_messages(created_payload)).lower()
        ):
            reconciled = self.client.get_json(branch_path, deadline=deadline)
            return self._branch_result(
                reconciled,
                project=project,
                project_result=project_result,
                branch=branch,
                source_ref=selected_source,
                created=False,
            )
        if status >= 400:
            raise self.client._error_for_status(status)

        def finish_branch_write():
            if status != 201:
                raise GitLabError("invalid_remote_data")
            created_item = _as_object(created_payload)
            if {"name", "commit", "web_url"}.issubset(created_item):
                return self._branch_result(
                    created_payload,
                    project=project,
                    project_result=project_result,
                    branch=branch,
                    source_ref=selected_source,
                    created=True,
                )
            partial_identity = self._validate_partial_branch_identity(
                created_item, project_result=project_result, branch=branch
            )
            reconciled = self.client.get_json(branch_path, deadline=deadline)
            result = self._branch_result(
                reconciled,
                project=project,
                project_result=project_result,
                branch=branch,
                source_ref=selected_source,
                created=True,
            )
            reconciled_identity = self._validate_partial_branch_identity(
                _as_object(reconciled),
                project_result=project_result,
                branch=branch,
            )
            self._require_reconciled_identity(
                partial_identity, reconciled_identity
            )
            return result

        return self._usable_write_result(finish_branch_write)

    def create_named_branch(
        self,
        project: str | int,
        *,
        branch: str,
        ref: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        branch = _validate_ref(branch)
        ref = _validate_ref(ref)
        if not isinstance(dry_run, bool):
            raise GitLabError("invalid_input")

        deadline = self.client.operation_deadline()
        project_result = self._write_project(project, deadline=deadline)
        endpoint = _project_endpoint(project_result["id"])
        commit_payload = _as_object(
            self.client.get_json(
                f"/api/v4/projects/{endpoint}/repository/commits/"
                f"{quote(ref, safe='')}",
                deadline=deadline,
            )
        )
        commit_id = _commit_sha(commit_payload.get("id"))
        if "project_id" in commit_payload and (
            _remote_positive_int(commit_payload.get("project_id"))
            != project_result["id"]
        ):
            raise GitLabError("invalid_remote_data")

        branch_path = (
            f"/api/v4/projects/{endpoint}/repository/branches/"
            f"{quote(branch, safe='')}"
        )

        def read_target() -> dict[str, Any] | None:
            try:
                payload = self.client.get_json(branch_path, deadline=deadline)
            except GitLabError as exc:
                if exc.category == "not_found":
                    return None
                raise
            return self._branch_result(
                payload,
                project=project,
                project_result=project_result,
                branch=branch,
                source_ref=ref,
                created=False,
            )

        existing = read_target()
        if existing is not None:
            if existing["commit_id"] != commit_id:
                raise GitLabError("conflict")
            if dry_run:
                existing["dry_run"] = True
            return existing
        if dry_run:
            return {
                "project": str(project),
                "branch": branch,
                "source_ref": ref,
                "commit_id": commit_id,
                "created": False,
                "reused": False,
                "dry_run": True,
            }

        status, created_payload = self._write_json(
            "POST",
            f"/api/v4/projects/{endpoint}/repository/branches",
            {"branch": branch, "ref": commit_id},
            deadline=deadline,
        )
        duplicate_race = (
            status in {400, 409}
            and "branch already exists"
            in " ".join(self._remote_messages(created_payload)).lower()
        )
        if status >= 400 and not duplicate_race:
            raise self.client._error_for_status(status)
        if status < 400 and status != 201:
            raise GitLabError("write_ambiguous")

        def reconcile_named_branch() -> dict[str, Any]:
            partial_identity: dict[str, Any] = {}
            if not duplicate_race:
                partial_identity = self._validate_partial_branch_identity(
                    _as_object(created_payload),
                    project_result=project_result,
                    branch=branch,
                )
            reconciled_payload = self.client.get_json(
                branch_path, deadline=deadline
            )
            result = self._branch_result(
                reconciled_payload,
                project=project,
                project_result=project_result,
                branch=branch,
                source_ref=ref,
                created=not duplicate_race,
            )
            if result["commit_id"] != commit_id:
                raise GitLabError("invalid_remote_data")
            if partial_identity:
                reconciled_identity = self._validate_partial_branch_identity(
                    _as_object(reconciled_payload),
                    project_result=project_result,
                    branch=branch,
                )
                self._require_reconciled_identity(
                    partial_identity, reconciled_identity
                )
            return result

        return self._usable_write_result(reconcile_named_branch)

    @staticmethod
    def _commit_actions(
        actions: Any,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        if not isinstance(actions, list) or not actions:
            raise GitLabError("invalid_input")
        if len(actions) > _MAX_WRITE_ACTIONS:
            raise GitLabError("capacity")
        normalized: list[dict[str, Any]] = []
        projected: list[dict[str, str]] = []
        aggregate_bytes = 0
        seen_paths: set[str] = set()
        for raw in actions:
            if not isinstance(raw, Mapping):
                raise GitLabError("invalid_input")
            action = raw.get("action")
            if action not in {"create", "update", "delete"}:
                raise GitLabError("invalid_input")
            allowed = {"action", "file_path"}
            if action in {"create", "update"}:
                allowed.add("content")
            if action in {"update", "delete"}:
                allowed.add("last_commit_id")
            if set(raw) - allowed:
                raise GitLabError("invalid_input")
            path = _validate_path(raw.get("file_path"), allow_empty=False)
            if path in seen_paths:
                raise GitLabError("invalid_input")
            seen_paths.add(path)
            item: dict[str, Any] = {"action": action, "file_path": path}
            if action in {"create", "update"}:
                content = raw.get("content")
                if not isinstance(content, str):
                    raise GitLabError("invalid_input")
                aggregate_bytes += len(content.encode("utf-8"))
                if aggregate_bytes > _MAX_WRITE_BYTES:
                    raise GitLabError("capacity")
                item["content"] = content
            if "last_commit_id" in raw:
                item["last_commit_id"] = _validate_ref(raw["last_commit_id"])
            normalized.append(item)
            projected.append({"action": action, "file_path": path})
        return normalized, projected

    def _head_file_last_commit(
        self,
        endpoint: str,
        *,
        branch: str,
        file_path: str,
        deadline: float,
    ) -> str | None:
        path = (
            f"/api/v4/projects/{endpoint}/repository/files/{quote(file_path, safe='')}"
        )
        self.client._validate_path(path)
        self.client._check_cancelled(deadline)
        try:
            with self.client._client.stream(
                "HEAD",
                path,
                params={"ref": branch},
                timeout=self.client._request_timeout(deadline),
            ) as response:
                self.client._check_cancelled(deadline)
                if 300 <= response.status_code < 400:
                    raise GitLabError("invalid_remote_data")
                if response.status_code == 404:
                    return None
                if response.status_code >= 400:
                    raise self.client._error_for_status(response.status_code)
                if response.status_code != 200:
                    raise GitLabError("invalid_remote_data")
                raw_identity = response.headers.get("x-gitlab-last-commit-id")
                if raw_identity is None:
                    raise GitLabError("conflict")
                try:
                    return _validate_ref(raw_identity)
                except GitLabError:
                    raise GitLabError("conflict") from None
        except GitLabError:
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            self.client._check_cancelled(deadline)
            raise GitLabError("transient") from None

    def _reconcile_commit_actions(
        self,
        actions: list[dict[str, Any]],
        *,
        endpoint: str,
        branch: str,
        deadline: float,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        reconciled: list[dict[str, Any]] = []
        projected: list[dict[str, str]] = []
        for requested in actions:
            item = dict(requested)
            remote_identity = self._head_file_last_commit(
                endpoint,
                branch=branch,
                file_path=item["file_path"],
                deadline=deadline,
            )
            action = item["action"]
            if action == "update" and remote_identity is None:
                item["action"] = "create"
                item.pop("last_commit_id", None)
            elif action == "create" and remote_identity is not None:
                item["action"] = "update"
                item["last_commit_id"] = remote_identity
            elif action == "delete" and remote_identity is None:
                raise GitLabError("conflict")
            elif remote_identity is not None:
                requested_identity = item.get("last_commit_id")
                if (
                    requested_identity is not None
                    and requested_identity != remote_identity
                ):
                    raise GitLabError("conflict")
                item["last_commit_id"] = remote_identity
            reconciled.append(item)
            projected.append({"action": item["action"], "file_path": item["file_path"]})
        return reconciled, projected

    def _commit_result(
        self,
        payload: Any,
        *,
        project: str | int,
        project_result: Mapping[str, Any] | None = None,
        branch: str,
        commit_message: str,
        actions: list[dict[str, str]],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if dry_run:
            return {
                "project": str(project),
                "branch": branch,
                "commit_message": commit_message,
                "action_count": len(actions),
                "actions": actions,
                "dry_run": True,
            }
        item = _as_object(payload)
        commit_id = _validate_remote_ref(item.get("id"))
        short_id = item.get("short_id")
        title = item.get("title")
        web_url = item.get("web_url")
        expected_title = commit_message.splitlines()[0]
        if (
            not isinstance(short_id, str)
            or not short_id
            or len(short_id) > 64
            or not commit_id.startswith(short_id)
            or title != expected_title
            or ("message" in item and item.get("message") != commit_message)
        ):
            raise GitLabError("invalid_remote_data")
        if project_result is None:
            raise GitLabError("invalid_remote_data")
        if "project_id" in item:
            remote_project_id = _remote_positive_int(item.get("project_id"))
            if remote_project_id != project_result["id"]:
                raise GitLabError("invalid_remote_data")
        if "branch" in item and item.get("branch") != branch:
            raise GitLabError("invalid_remote_data")
        web_url = _canonical_remote_url(
            web_url,
            self.client.auth.origin,
            f"/{project_result['path_with_namespace']}/-/commit/{commit_id}",
        )
        return {
            "project": str(project),
            "branch": branch,
            "commit_id": commit_id,
            "short_id": short_id,
            "title": title,
            "web_url": web_url,
            "action_count": len(actions),
            "actions": actions,
            "dry_run": False,
        }

    def _validate_partial_commit_identity(
        self,
        payload: Mapping[str, Any],
        *,
        project_result: Mapping[str, Any],
        branch: str,
        commit_message: str,
    ) -> dict[str, Any]:
        commit_id = _validate_remote_ref(payload.get("id"))
        identity: dict[str, Any] = {"id": commit_id}
        expected_title = commit_message.splitlines()[0]
        if "title" in payload and payload.get("title") != expected_title:
            raise GitLabError("invalid_remote_data")
        if "title" in payload:
            identity["title"] = expected_title
        if "message" in payload and payload.get("message") != commit_message:
            raise GitLabError("invalid_remote_data")
        if "message" in payload:
            identity["message"] = commit_message
        if "short_id" in payload:
            short_id = payload.get("short_id")
            if (
                not isinstance(short_id, str)
                or not short_id
                or len(short_id) > 64
                or not commit_id.startswith(short_id)
            ):
                raise GitLabError("invalid_remote_data")
            identity["short_id"] = short_id
        if "project_id" in payload:
            remote_project_id = _remote_positive_int(payload.get("project_id"))
            if remote_project_id != project_result["id"]:
                raise GitLabError("invalid_remote_data")
            identity["project_id"] = remote_project_id
        if "branch" in payload and payload.get("branch") != branch:
            raise GitLabError("invalid_remote_data")
        if "branch" in payload:
            identity["branch"] = branch
        if "web_url" in payload:
            identity["web_url"] = _canonical_remote_url(
                payload.get("web_url"),
                self.client.auth.origin,
                f"/{project_result['path_with_namespace']}/-/commit/{commit_id}",
            )
        return identity

    @staticmethod
    def _remote_messages(payload: Any) -> list[str]:
        if not isinstance(payload, Mapping):
            return []
        message = payload.get("message")
        if isinstance(message, str):
            return [message[:4096]]
        if isinstance(message, list) and len(message) <= 20:
            return [value[:4096] for value in message if isinstance(value, str)]
        if isinstance(message, Mapping) and len(message) <= 20:
            values: list[str] = []
            for nested in message.values():
                if isinstance(nested, str):
                    values.append(nested[:4096])
                elif isinstance(nested, list) and len(nested) <= 20:
                    values.extend(
                        value[:4096] for value in nested if isinstance(value, str)
                    )
            return values
        return []

    def commit_changes(
        self,
        project: str | int,
        *,
        branch: str,
        commit_message: str,
        actions: Any,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        endpoint = _project_endpoint(project)
        branch = _validate_ref(branch)
        commit_message = _bounded_string(commit_message, _MAX_COMMIT_MESSAGE)
        normalized, _requested_projection = self._commit_actions(actions)
        if not isinstance(dry_run, bool):
            raise GitLabError("invalid_input")
        deadline = self.client.operation_deadline()
        project_result = None
        if not dry_run:
            project_result = self._write_project(project, deadline=deadline)
        normalized, projected = self._reconcile_commit_actions(
            normalized,
            endpoint=endpoint,
            branch=branch,
            deadline=deadline,
        )
        if dry_run:
            return self._commit_result(
                {},
                project=project,
                branch=branch,
                commit_message=commit_message,
                actions=projected,
                dry_run=True,
            )
        status, payload = self._write_json(
            "POST",
            f"/api/v4/projects/{endpoint}/repository/commits",
            {
                "branch": branch,
                "commit_message": commit_message,
                "actions": normalized,
            },
            deadline=deadline,
        )
        if status >= 400:
            messages = " ".join(self._remote_messages(payload)).lower()
            if status in {400, 409} and "last_commit_id" in messages:
                raise GitLabError("conflict")
            raise self.client._error_for_status(status)

        def finish_commit_write():
            if status != 201:
                raise GitLabError("invalid_remote_data")
            item = _as_object(payload)
            if {"id", "short_id", "title", "web_url"}.issubset(item):
                return self._commit_result(
                    payload,
                    project=project,
                    project_result=project_result,
                    branch=branch,
                    commit_message=commit_message,
                    actions=projected,
                )
            if project_result is None:
                raise GitLabError("invalid_remote_data")
            partial_identity = self._validate_partial_commit_identity(
                item,
                project_result=project_result,
                branch=branch,
                commit_message=commit_message,
            )
            commit_id = partial_identity["id"]
            reconciled = self.client.get_json(
                f"/api/v4/projects/{endpoint}/repository/commits/"
                f"{quote(commit_id, safe='')}",
                deadline=deadline,
            )
            result = self._commit_result(
                reconciled,
                project=project,
                project_result=project_result,
                branch=branch,
                commit_message=commit_message,
                actions=projected,
            )
            reconciled_identity = self._validate_partial_commit_identity(
                _as_object(reconciled),
                project_result=project_result,
                branch=branch,
                commit_message=commit_message,
            )
            self._require_reconciled_identity(
                partial_identity, reconciled_identity
            )
            return result

        return self._usable_write_result(finish_commit_write)

    def _merge_request_result(
        self,
        payload: Any,
        *,
        project: str | int,
        project_id: int,
        project_path: str,
        source_branch: str,
        target_branch: str,
        created: bool,
        expected_title: str | None = None,
    ) -> dict[str, Any]:
        item = _as_object(payload)
        iid = _remote_positive_int(item.get("iid"))
        remote_project_id = _remote_positive_int(item.get("project_id"))
        title = item.get("title")
        state = item.get("state")
        source = item.get("source_branch")
        target = item.get("target_branch")
        web_url = item.get("web_url")
        if (
            remote_project_id != project_id
            or not isinstance(title, str)
            or not title
            or len(title) > _MAX_MR_TITLE
            or (expected_title is not None and title != expected_title)
            or state != "opened"
            or source != source_branch
            or target != target_branch
        ):
            raise GitLabError("invalid_remote_data")
        web_url = _canonical_remote_url(
            web_url,
            self.client.auth.origin,
            f"/{project_path}/-/merge_requests/{iid}",
        )
        return {
            "project": str(project),
            "iid": iid,
            "title": title,
            "state": state,
            "source_branch": source,
            "target_branch": target,
            "web_url": web_url,
            "created": created,
            "reused": not created,
            "dry_run": False,
        }

    def _validate_partial_merge_request_identity(
        self,
        payload: Mapping[str, Any],
        *,
        project_id: int,
        project_path: str,
        source_branch: str,
        target_branch: str,
        title: str,
    ) -> dict[str, Any]:
        iid = _remote_positive_int(payload.get("iid"))
        identity: dict[str, Any] = {"iid": iid}
        expected = {
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": title,
            "state": "opened",
        }
        for field, expected_value in expected.items():
            if field in payload and payload.get(field) != expected_value:
                raise GitLabError("invalid_remote_data")
            if field in payload:
                identity[field] = expected_value
        if "project_id" in payload:
            remote_project_id = _remote_positive_int(payload.get("project_id"))
            if remote_project_id != project_id:
                raise GitLabError("invalid_remote_data")
            identity["project_id"] = remote_project_id
        if "web_url" in payload:
            identity["web_url"] = _canonical_remote_url(
                payload.get("web_url"),
                self.client.auth.origin,
                f"/{project_path}/-/merge_requests/{iid}",
            )
        return identity

    def _existing_merge_request(
        self,
        project: str | int,
        project_id: int,
        project_path: str,
        endpoint: str,
        source_branch: str,
        target_branch: str,
        *,
        deadline: float,
    ) -> dict[str, Any]:
        payload = _as_list(
            self.client.get_json(
                f"/api/v4/projects/{endpoint}/merge_requests",
                params={
                    "scope": "all",
                    "state": "opened",
                    "source_branch": source_branch,
                    "target_branch": target_branch,
                },
                deadline=deadline,
            )
        )
        if len(payload) != 1:
            raise GitLabError("conflict")
        try:
            return self._merge_request_result(
                payload[0],
                project=project,
                project_id=project_id,
                project_path=project_path,
                source_branch=source_branch,
                target_branch=target_branch,
                created=False,
            )
        except GitLabError:
            raise GitLabError("conflict") from None

    def create_merge_request(
        self,
        project: str | int,
        *,
        source_branch: str,
        target_branch: str | None = None,
        title: str | None = None,
        description: str = "",
        remove_source_branch: bool = True,
        squash: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        source_branch = _validate_ref(source_branch)
        if target_branch is not None:
            target_branch = _validate_ref(target_branch)
            if target_branch == source_branch:
                raise GitLabError("invalid_input")
        if title is not None:
            title = _bounded_string(title, _MAX_MR_TITLE_INPUT)[:_MAX_MR_TITLE]
        description = _bounded_string(
            description, _MAX_MR_DESCRIPTION, allow_empty=True
        )
        description = _reject_hidden_quick_actions(description)
        if not all(
            isinstance(value, bool) for value in (remove_source_branch, squash, dry_run)
        ):
            raise GitLabError("invalid_input")
        deadline = self.client.operation_deadline()
        project_result = self._write_project(project, deadline=deadline)
        selected_target = target_branch or project_result["default_branch"]
        if selected_target == source_branch:
            raise GitLabError("invalid_input")
        selected_title = (title or re.sub(r"[-_/]+", " ", source_branch).capitalize())[
            :_MAX_MR_TITLE
        ]
        if not selected_title or len(selected_title) > _MAX_MR_TITLE:
            raise GitLabError("invalid_input")
        if dry_run:
            return {
                "project": str(project),
                "source_branch": source_branch,
                "target_branch": selected_target,
                "title": selected_title,
                "description_present": bool(description),
                "remove_source_branch": remove_source_branch,
                "squash": squash,
                "created": False,
                "reused": False,
                "dry_run": True,
            }
        endpoint = _project_endpoint(project_result["id"])
        status, payload = self._write_json(
            "POST",
            f"/api/v4/projects/{endpoint}/merge_requests",
            {
                "source_branch": source_branch,
                "target_branch": selected_target,
                "title": selected_title,
                "description": description,
                "remove_source_branch": remove_source_branch,
                "squash": squash,
            },
            deadline=deadline,
        )
        if status in {400, 409}:
            messages = " ".join(self._remote_messages(payload)).lower()
            if _DUPLICATE_MR_MESSAGE in messages:
                return self._existing_merge_request(
                    project,
                    project_result["id"],
                    project_result["path_with_namespace"],
                    endpoint,
                    source_branch,
                    selected_target,
                    deadline=deadline,
                )
            raise self.client._error_for_status(status)

        def finish_merge_request_write():
            if status != 201:
                raise GitLabError("invalid_remote_data")
            item = _as_object(payload)
            if {
                "iid",
                "project_id",
                "title",
                "state",
                "source_branch",
                "target_branch",
                "web_url",
            }.issubset(item):
                return self._merge_request_result(
                    payload,
                    project=project,
                    project_id=project_result["id"],
                    project_path=project_result["path_with_namespace"],
                    source_branch=source_branch,
                    target_branch=selected_target,
                    created=True,
                    expected_title=selected_title,
                )
            partial_identity = self._validate_partial_merge_request_identity(
                item,
                project_id=project_result["id"],
                project_path=project_result["path_with_namespace"],
                source_branch=source_branch,
                target_branch=selected_target,
                title=selected_title,
            )
            iid = partial_identity["iid"]
            reconciled = self.client.get_json(
                f"/api/v4/projects/{endpoint}/merge_requests/{iid}",
                deadline=deadline,
            )
            result = self._merge_request_result(
                reconciled,
                project=project,
                project_id=project_result["id"],
                project_path=project_result["path_with_namespace"],
                source_branch=source_branch,
                target_branch=selected_target,
                created=True,
                expected_title=selected_title,
            )
            reconciled_identity = self._validate_partial_merge_request_identity(
                _as_object(reconciled),
                project_id=project_result["id"],
                project_path=project_result["path_with_namespace"],
                source_branch=source_branch,
                target_branch=selected_target,
                title=selected_title,
            )
            self._require_reconciled_identity(
                partial_identity, reconciled_identity
            )
            return result

        return self._usable_write_result(finish_merge_request_write)

    @staticmethod
    def _add_warning(warnings: list[str], code: str) -> None:
        if code not in warnings:
            warnings.append(code)

    def _ci_project(self, project: str | int, *, deadline: float) -> dict[str, Any]:
        endpoint = _project_endpoint(project)
        payload = _as_object(
            self.client.get_json(f"/api/v4/projects/{endpoint}", deadline=deadline)
        )
        project_id = payload.get("id")
        path = payload.get("path_with_namespace")
        name = payload.get("name")
        web_url = payload.get("web_url")
        namespace_value = payload.get("namespace")
        namespace = (
            namespace_value.get("full_path")
            if isinstance(namespace_value, Mapping)
            else None
        )
        if (
            isinstance(project_id, bool)
            or not isinstance(project_id, int)
            or project_id <= 0
            or not isinstance(path, str)
            or "/" not in path
            or len(path) > _MAX_PROJECT_SLUG
            or not isinstance(name, str)
            or len(name) > 512
            or not isinstance(namespace, str)
            or not namespace
            or len(namespace) > _MAX_PROJECT_SLUG
        ):
            raise GitLabError("invalid_remote_data")
        _same_origin_url(web_url, self.client.auth.origin)
        default = payload.get("default_branch")
        fallback = default is None or default == ""
        if not fallback and not isinstance(default, str):
            raise GitLabError("invalid_remote_data")
        default_branch = "main" if fallback else _validate_remote_ref(default)
        return {
            "id": project_id,
            "name": name,
            "path_with_namespace": path,
            "namespace": namespace,
            "default_branch": default_branch,
            "default_branch_fallback": fallback,
            "web_url": f"{self.client.auth.origin}/{quote(path, safe='/')}",
        }

    def _ci_pages(
        self,
        path: str,
        *,
        params: Mapping[str, Any],
        deadline: float,
        max_pages: int,
        max_items: int,
    ) -> tuple[list[Mapping[str, Any]], bool, dict[str, int] | None]:
        items: list[Mapping[str, Any]] = []
        page = 1
        while page <= max_pages:
            query = dict(params)
            query.update({"per_page": 100, "page": page})
            payload, headers = self.client.get_json_page(
                path, params=query, deadline=deadline
            )
            values = _as_list(payload)
            for offset, raw in enumerate(values):
                if len(items) >= max_items:
                    return items, True, {"page": page, "offset": offset}
                items.append(_as_object(raw))
            next_header = str(headers.get("x-next-page", "")).strip()
            if not next_header and len(values) < 100:
                return items, False, None
            candidate = page + 1
            if next_header:
                try:
                    candidate = int(next_header)
                except ValueError:
                    raise GitLabError("invalid_remote_data") from None
                if candidate <= page:
                    raise GitLabError("invalid_remote_data")
            if candidate > max_pages:
                return items, True, {"next_page": candidate}
            page = candidate
        return items, False, None

    def _pipeline_window(
        self,
        project_id: int,
        *,
        start: datetime,
        end: datetime,
        deadline: float,
    ) -> dict[str, Any]:
        path = f"/api/v4/projects/{project_id}/pipelines"
        _body, count_headers = self.client.get_json_page(
            path,
            params={"updated_after": start.isoformat(), "per_page": 1},
            deadline=deadline,
        )
        count_header = count_headers.get("x-total")
        if count_header is None or str(count_header) == "":
            count = None
            count_status = "missing"
        elif str(count_header).isdigit() and len(str(count_header)) <= 10:
            count = int(str(count_header))
            count_status = "reported"
        else:
            count = None
            count_status = "malformed"
        latest_payload = _as_list(
            self.client.get_json(
                path,
                params={"per_page": 1, "order_by": "updated_at", "sort": "desc"},
                deadline=deadline,
            )
        )
        latest_status = None
        if latest_payload:
            latest = _as_object(latest_payload[0])
            latest_status = latest.get("status")
            if (
                not isinstance(latest_status, str)
                or not latest_status
                or len(latest_status) > 64
            ):
                raise GitLabError("invalid_remote_data")
        return {
            "count": count,
            "count_status": count_status,
            "latest_status": latest_status,
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
        }

    def _select_ci_branches(
        self,
        project_id: int,
        branch_spec: str,
        *,
        start: datetime,
        deadline: float,
        max_pages: int,
        max_branches: int,
        warnings: list[str],
    ) -> tuple[list[str], bool, list[dict[str, Any]]]:
        mode = branch_spec.upper()
        if mode not in {"ALL", "RECENT"}:
            return [_validate_ref(branch_spec)], False, []
        pipeline_params: dict[str, Any] = {
            "order_by": "updated_at",
            "sort": "desc",
        }
        if mode == "RECENT":
            pipeline_params["updated_after"] = start.isoformat()
        pipeline_items, pipeline_truncated, pipeline_continuation = self._ci_pages(
            f"/api/v4/projects/{project_id}/pipelines",
            params=pipeline_params,
            deadline=deadline,
            max_pages=max_pages,
            max_items=min(_MAX_PIPELINES, max_branches * 10),
        )
        live_items, live_truncated, live_continuation = self._ci_pages(
            f"/api/v4/projects/{project_id}/repository/branches",
            params={},
            deadline=deadline,
            max_pages=max_pages,
            max_items=_MAX_TREE_ITEMS,
        )
        live: set[str] = set()
        for item in live_items:
            live.add(_validate_remote_ref(item.get("name")))
        selected: list[str] = []
        seen: set[str] = set()
        for item in pipeline_items:
            ref = _validate_remote_ref(item.get("ref"))
            if ref in live and ref not in seen:
                seen.add(ref)
                selected.append(ref)
        truncated = pipeline_truncated or live_truncated or len(selected) > max_branches
        if pipeline_truncated:
            self._add_warning(warnings, "pipeline_branch_inventory_truncated")
        if live_truncated:
            self._add_warning(warnings, "live_branch_inventory_truncated")
        if len(selected) > max_branches:
            self._add_warning(warnings, "branch_limit_reached")
        continuations: list[dict[str, Any]] = []
        if pipeline_continuation is not None:
            continuations.append({"source": "pipelines", **pipeline_continuation})
        if live_continuation is not None:
            continuations.append({"source": "live_branches", **live_continuation})
        if len(selected) > max_branches:
            continuations.append(
                {"source": "selected_branches", "offset": max_branches}
            )
        return selected[:max_branches], truncated, continuations

    def _ci_text_file(
        self,
        project: str | int,
        path: str,
        ref: str,
        *,
        max_bytes: int,
        deadline: float,
    ) -> dict[str, Any]:
        endpoint = _project_endpoint(project)
        normalized_path = path[1:] if path.startswith("/") else path
        normalized_path = _validate_path(normalized_path, allow_empty=False)
        ref = _validate_ref(ref)
        encoded_path = quote(normalized_path, safe="")
        payload = _as_object(
            self.client.get_json(
                f"/api/v4/projects/{endpoint}/repository/files/{encoded_path}",
                params={"ref": ref},
                deadline=deadline,
            )
        )
        content = payload.get("content")
        size = payload.get("size")
        if payload.get("encoding") != "base64" or not isinstance(content, str):
            raise GitLabError("invalid_remote_data")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise GitLabError("invalid_remote_data")
        if size > max_bytes:
            raise GitLabError("capacity")
        try:
            decoded = base64.b64decode("".join(content.split()), validate=True)
        except (binascii.Error, ValueError):
            raise GitLabError("invalid_remote_data") from None
        if len(decoded) > max_bytes:
            raise GitLabError("capacity")
        try:
            text = decoded.decode("utf-8")
        except UnicodeDecodeError:
            raise GitLabError("invalid_remote_data") from None
        return {
            "path": normalized_path,
            "text": text,
            "size": len(decoded),
            "sha256": hashlib.sha256(decoded).hexdigest(),
        }

    @staticmethod
    def _include_specs(text: str) -> tuple[list[dict[str, Any]], str]:
        loader = _BoundedComposeLoader(text)
        try:
            root = loader.get_single_node()
        except _YamlCapacityError:
            return [], "capacity"
        except yaml.YAMLError:
            return [], "invalid"
        finally:
            loader.dispose()
        if root is None:
            return [], "ok"
        if not isinstance(root, yaml.nodes.MappingNode):
            return [], "invalid"
        include_node: yaml.nodes.Node | None = None
        for key_node, value_node in root.value:
            if (
                isinstance(key_node, yaml.nodes.ScalarNode)
                and key_node.value == "include"
            ):
                include_node = value_node
                break
        if include_node is None:
            return [], "ok"
        raw_nodes = (
            include_node.value
            if isinstance(include_node, yaml.nodes.SequenceNode)
            else [include_node]
        )
        specs: list[dict[str, Any]] = []
        for item in raw_nodes:
            if isinstance(item, yaml.nodes.ScalarNode):
                specs.append({"type": "local", "file": item.value})
                continue
            if not isinstance(item, yaml.nodes.MappingNode):
                return [], "invalid"
            fields: dict[str, str] = {}
            for key_node, value_node in item.value:
                if not isinstance(key_node, yaml.nodes.ScalarNode) or not isinstance(
                    value_node, yaml.nodes.ScalarNode
                ):
                    return [], "invalid"
                fields[key_node.value] = value_node.value
            if "local" in fields:
                specs.append({"type": "local", "file": fields["local"]})
            elif "project" in fields and "file" in fields:
                specs.append(
                    {
                        "type": "project",
                        "project": fields["project"],
                        "file": fields["file"],
                        "ref": fields.get("ref", "main"),
                    }
                )
            elif "remote" in fields:
                specs.append({"type": "remote", "location": fields["remote"]})
            elif "template" in fields:
                specs.append({"type": "template", "location": fields["template"]})
        return specs, "ok"

    def _resolve_ci_includes(
        self,
        text: str,
        *,
        project_id: int,
        project_path: str,
        branch: str,
        deadline: float,
        budget: dict[str, int],
        warnings: list[str],
    ) -> tuple[list[dict[str, Any]], bool, str]:
        specs, parse_status = self._include_specs(text)
        if parse_status != "ok":
            self._add_warning(warnings, f"ci_yaml_{parse_status}")
            return [], parse_status == "capacity", parse_status
        available_count = budget["count"]
        truncated = len(specs) > available_count
        if truncated:
            self._add_warning(warnings, "include_limit_reached")
        root_identity = (project_path, ".gitlab-ci.yml", branch)
        results: list[dict[str, Any]] = []
        selected_specs = specs[:available_count]
        budget["count"] -= len(selected_specs)
        for spec in selected_specs:
            kind = spec["type"]
            if kind in {"remote", "template"}:
                location = spec.get("location")
                if (
                    not isinstance(location, str)
                    or not location
                    or len(location) > _MAX_PATH
                ):
                    raise GitLabError("invalid_remote_data")
                results.append(
                    {
                        "type": kind,
                        "location": location,
                        "status": "unsupported",
                        "warning": "unsupported_include",
                    }
                )
                self._add_warning(warnings, "unsupported_include")
                continue
            include_warnings: list[str] = []
            file_value = spec.get("file")
            if (
                not isinstance(file_value, str)
                or not file_value
                or len(file_value) > _MAX_PATH
            ):
                raise GitLabError("invalid_remote_data")
            file_path = file_value[1:] if file_value.startswith("/") else file_value
            file_path = _validate_path(file_path, allow_empty=False)
            include_project: str | int
            if kind == "local":
                include_project = project_id
                identity_project = project_path
                include_ref = branch
            else:
                include_project = spec.get("project")
                if not isinstance(include_project, (str, int)) or isinstance(
                    include_project, bool
                ):
                    raise GitLabError("invalid_remote_data")
                include_endpoint = _project_endpoint(include_project)
                current_endpoints = {
                    str(project_id),
                    quote(project_path, safe=""),
                }
                if include_endpoint in current_endpoints:
                    include_project = project_id
                    identity_project = project_path
                else:
                    identity_project = str(include_project)
                raw_ref = spec.get("ref", "main")
                if (
                    raw_ref is None
                    or raw_ref == ""
                    or (isinstance(raw_ref, str) and raw_ref.startswith("$"))
                ):
                    include_ref = "main"
                    include_warnings.append("include_ref_not_interpolated")
                elif isinstance(raw_ref, str):
                    include_ref = _validate_ref(raw_ref)
                else:
                    raise GitLabError("invalid_remote_data")
            identity = (identity_project, file_path, include_ref)
            base_result: dict[str, Any] = {
                "type": kind,
                "project": identity_project,
                "file": file_path,
                "ref": include_ref,
                "warnings": include_warnings,
            }
            if identity == root_identity:
                results.append(
                    {**base_result, "status": "cycle", "sha256": None, "size": 0}
                )
                self._add_warning(warnings, "include_cycle")
                continue
            if budget["bytes"] == 0:
                results.append(
                    {**base_result, "status": "capacity", "sha256": None, "size": 0}
                )
                self._add_warning(warnings, "include_capacity")
                truncated = True
                continue
            try:
                fetched = self._ci_text_file(
                    include_project,
                    file_path,
                    include_ref,
                    max_bytes=budget["bytes"],
                    deadline=deadline,
                )
                budget["bytes"] -= fetched["size"]
                results.append(
                    {
                        **base_result,
                        "status": "success",
                        "sha256": fetched["sha256"],
                        "size": fetched["size"],
                    }
                )
            except GitLabError as exc:
                status = {
                    "not_found": "not_found",
                    "permission": "permission",
                    "capacity": "capacity",
                    "cancelled": "cancelled",
                    "deadline": "deadline",
                }.get(exc.category, "transient")
                if exc.category in {"cancelled", "deadline"}:
                    raise
                results.append(
                    {**base_result, "status": status, "sha256": None, "size": 0}
                )
                self._add_warning(warnings, f"include_{status}")
                if status == "capacity":
                    truncated = True
        return results, truncated, parse_status

    @staticmethod
    def _variable_metadata(
        raw: Mapping[str, Any], *, scope: str, source: str
    ) -> dict[str, Any]:
        key = raw.get("key")
        variable_type = raw.get("variable_type", "env_var")
        environment_scope = raw.get("environment_scope", "*")
        description = raw.get("description")
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 512
            or not isinstance(variable_type, str)
            or not variable_type
            or len(variable_type) > 64
            or not isinstance(environment_scope, str)
            or not environment_scope
            or len(environment_scope) > 512
            or (
                description is not None
                and (not isinstance(description, str) or len(description) > 2048)
            )
        ):
            raise GitLabError("invalid_remote_data")
        flags = {}
        for source_name, target_name in (
            ("protected", "protected"),
            ("masked", "masked"),
            ("hidden", "hidden"),
            ("raw", "raw"),
        ):
            value = raw.get(source_name, False)
            if not isinstance(value, bool):
                raise GitLabError("invalid_remote_data")
            flags[target_name] = value
        return {
            "key": key,
            "type": variable_type,
            **flags,
            "environment_scope": environment_scope,
            "description": description,
            "scope": scope,
            "source": source,
        }

    def list_ci_variables(
        self,
        project: str | int,
        *,
        max_items: int = 100,
        continuation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        max_items = _positive_bound(max_items, _MAX_CI_VARIABLES)
        start_page, start_offset = _continuation_source(continuation)
        deadline = self.client.operation_deadline()
        resolved = self.resolve_project(project, deadline=deadline)

        def normalize(raw: Mapping[str, Any]) -> dict[str, Any]:
            metadata = self._variable_metadata(
                raw, scope="project", source=resolved["path_with_namespace"]
            )
            return {
                key: metadata[key]
                for key in (
                    "key",
                    "type",
                    "protected",
                    "masked",
                    "hidden",
                    "raw",
                    "environment_scope",
                    "description",
                )
            }

        pages = self._paginate(
            f"/api/v4/projects/{resolved['id']}/variables",
            params={},
            max_items=max_items,
            normalize=normalize,
            deadline=deadline,
            start_page=start_page,
            start_offset=start_offset,
        )
        return {
            "project": {"id": resolved["id"], "path": resolved["path_with_namespace"]},
            "variables": list(pages.items),
            "count": len(pages.items),
            "truncated": pages.truncated,
            "continuation": self._continuation(pages),
        }

    def _collect_variable_endpoint(
        self,
        path: str,
        *,
        scope: str,
        source: str,
        deadline: float,
        max_pages: int,
        max_variables: int,
        items: list[dict[str, Any]],
        identities: set[tuple[str, str, str, str]],
        continuations: list[dict[str, Any]],
    ) -> bool:
        page = 1
        while page <= max_pages:
            payload, headers = self.client.get_json_page(
                path,
                params={"per_page": 100, "page": page},
                deadline=deadline,
            )
            values = _as_list(payload)
            for offset, raw in enumerate(values):
                if len(items) >= max_variables:
                    continuations.append(
                        {
                            "scope": scope,
                            "source": source,
                            "page": page,
                            "offset": offset,
                        }
                    )
                    return True
                metadata = self._variable_metadata(
                    _as_object(raw), scope=scope, source=source
                )
                identity = (
                    metadata["key"],
                    metadata["environment_scope"],
                    scope,
                    source,
                )
                if identity not in identities:
                    identities.add(identity)
                    items.append(metadata)
            next_header = str(headers.get("x-next-page", "")).strip()
            if not next_header and len(values) < 100:
                return False
            candidate = page + 1
            if next_header:
                try:
                    candidate = int(next_header)
                except ValueError:
                    raise GitLabError("invalid_remote_data") from None
                if candidate <= page:
                    raise GitLabError("invalid_remote_data")
            if candidate > max_pages:
                continuations.append(
                    {"scope": scope, "source": source, "next_page": candidate}
                )
                return True
            page = candidate
        return False

    def _collect_ci_variables(
        self,
        project: Mapping[str, Any],
        *,
        deadline: float,
        max_pages: int,
        max_groups: int,
        max_variables: int,
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        identities: set[tuple[str, str, str, str]] = set()
        warnings: list[str] = []
        continuations: list[dict[str, Any]] = []
        permission_records: list[dict[str, Any]] = []
        truncated = False
        project_source = project["path_with_namespace"]
        try:
            truncated = self._collect_variable_endpoint(
                f"/api/v4/projects/{project['id']}/variables",
                scope="project",
                source=project_source,
                deadline=deadline,
                max_pages=max_pages,
                max_variables=max_variables,
                items=items,
                identities=identities,
                continuations=continuations,
            )
        except GitLabError as exc:
            if exc.category in {"cancelled", "deadline"}:
                raise
            if exc.category == "permission":
                permission_records.append(
                    {
                        "category": "permission",
                        "scope": "project",
                        "source": project_source,
                    }
                )
            self._add_warning(
                warnings,
                "project_variable_permission"
                if exc.category == "permission"
                else "project_variable_error",
            )
        ancestors = [
            "/".join(project["namespace"].split("/")[:index])
            for index in range(1, len(project["namespace"].split("/")) + 1)
        ]
        groups_truncated = len(ancestors) > max_groups
        if groups_truncated:
            truncated = True
        for ancestor in ancestors[:max_groups]:
            if len(items) >= max_variables:
                truncated = True
                break
            encoded = quote(ancestor, safe="")
            try:
                group = _as_object(
                    self.client.get_json(f"/api/v4/groups/{encoded}", deadline=deadline)
                )
                group_id = group.get("id")
                if (
                    isinstance(group_id, bool)
                    or not isinstance(group_id, int)
                    or group_id <= 0
                ):
                    raise GitLabError("invalid_remote_data")
                endpoint_truncated = self._collect_variable_endpoint(
                    f"/api/v4/groups/{group_id}/variables",
                    scope="group",
                    source=ancestor,
                    deadline=deadline,
                    max_pages=max_pages,
                    max_variables=max_variables,
                    items=items,
                    identities=identities,
                    continuations=continuations,
                )
                truncated = truncated or endpoint_truncated
            except GitLabError as exc:
                if exc.category in {"cancelled", "deadline"}:
                    raise
                if exc.category == "permission":
                    permission_records.append(
                        {
                            "category": "permission",
                            "scope": "group",
                            "source": ancestor,
                            "ancestor": ancestor,
                        }
                    )
                self._add_warning(
                    warnings,
                    "group_variable_permission"
                    if exc.category == "permission"
                    else "group_variable_error",
                )
        return {
            "items": items,
            "count": len(items),
            "truncated": truncated,
            "groups_truncated": groups_truncated,
            "continuations": continuations,
            "permission_records": permission_records,
            "warnings": warnings,
        }

    def inspect_ci(
        self,
        project: str | int,
        *,
        branch_spec: str = "RECENT",
        lookback_days: int = 10,
        collect_variables: bool = True,
        max_branches: int = 20,
        max_pages: int = 5,
        max_includes: int = 20,
        max_include_bytes: int = 128 * 1024,
        max_groups: int = 10,
        max_variables: int = 500,
    ) -> dict[str, Any]:
        branch_spec = _bounded_string(branch_spec, _MAX_REF)
        lookback_days = _positive_bound(lookback_days, 365)
        max_branches = _positive_bound(max_branches, _MAX_CI_BRANCHES)
        max_pages = _positive_bound(max_pages, _MAX_CI_PAGES)
        max_includes = _positive_bound(max_includes, _MAX_CI_INCLUDES)
        max_include_bytes = _positive_bound(max_include_bytes, _MAX_CI_INCLUDE_BYTES)
        max_groups = _positive_bound(max_groups, _MAX_CI_GROUPS)
        max_variables = _positive_bound(max_variables, _MAX_CI_VARIABLES)
        if not isinstance(collect_variables, bool):
            raise GitLabError("invalid_input")
        deadline = self.client.operation_deadline()
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=lookback_days)
        warnings: list[str] = []
        project_result = self._ci_project(project, deadline=deadline)
        pipeline_window = self._pipeline_window(
            project_result["id"], start=start, end=now, deadline=deadline
        )
        branch_names, branch_selection_truncated, branch_continuations = (
            self._select_ci_branches(
                project_result["id"],
                branch_spec,
                start=start,
                deadline=deadline,
                max_pages=max_pages,
                max_branches=max_branches,
                warnings=warnings,
            )
        )
        truncated = branch_selection_truncated
        branches: list[dict[str, Any]] = []
        include_budget = {"count": max_includes, "bytes": max_include_bytes}
        for branch in branch_names:
            fetched_at = datetime.now(timezone.utc).isoformat()
            try:
                fetched = self._ci_text_file(
                    project_result["id"],
                    ".gitlab-ci.yml",
                    branch,
                    max_bytes=_MAX_FILE_BYTES,
                    deadline=deadline,
                )
                includes, includes_truncated, include_parse_status = (
                    self._resolve_ci_includes(
                        fetched["text"],
                        project_id=project_result["id"],
                        project_path=project_result["path_with_namespace"],
                        branch=branch,
                        deadline=deadline,
                        budget=include_budget,
                        warnings=warnings,
                    )
                )
                truncated = truncated or includes_truncated
                ci_file = {
                    "status": "success",
                    "path": ".gitlab-ci.yml",
                    "sha256": fetched["sha256"],
                    "size": fetched["size"],
                    "fetched_at": fetched_at,
                    "includes": includes,
                    "includes_truncated": includes_truncated,
                    "include_parse_status": include_parse_status,
                }
            except GitLabError as exc:
                if exc.category in {"cancelled", "deadline"}:
                    raise
                status = {
                    "not_found": "not_found",
                    "permission": "permission",
                    "capacity": "capacity",
                }.get(exc.category, "transient")
                self._add_warning(warnings, f"ci_file_{status}")
                ci_file = {
                    "status": status,
                    "path": ".gitlab-ci.yml",
                    "sha256": None,
                    "size": 0,
                    "fetched_at": fetched_at,
                    "includes": [],
                    "includes_truncated": False,
                    "include_parse_status": "not_parsed",
                }
            branches.append({"name": branch, "ci_file": ci_file})
        variables = (
            self._collect_ci_variables(
                project_result,
                deadline=deadline,
                max_pages=max_pages,
                max_groups=max_groups,
                max_variables=max_variables,
            )
            if collect_variables
            else {
                "items": [],
                "count": 0,
                "truncated": False,
                "groups_truncated": False,
                "continuations": [],
                "permission_records": [],
                "warnings": [],
            }
        )
        truncated = truncated or variables["truncated"]
        return {
            "project": project_result,
            "branch_spec": branch_spec.upper()
            if branch_spec.upper() in {"ALL", "RECENT"}
            else branch_spec,
            "branch_selection": {
                "truncated": branch_selection_truncated,
                "continuations": branch_continuations,
            },
            "lookback_days": lookback_days,
            "pipeline_window": pipeline_window,
            "branches": branches,
            "variables": variables,
            "warnings": warnings,
            "truncated": truncated,
        }
