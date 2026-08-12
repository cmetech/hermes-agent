"""Bounded deterministic GitLab repository read operations."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping
from urllib.parse import quote, unquote, urlsplit

import yaml
import httpx

if __package__:
    from .client import GitLabClient
    from .models import GitLabError, PageResult
else:  # Standalone source tests import modules directly from the plugin root.
    from client import GitLabClient
    from models import GitLabError, PageResult


_MAX_PROJECT_REFERENCE = 2048
_MAX_PROJECT_SLUG = 1024
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
_MAX_WRITE_ACTIONS = 100
_MAX_WRITE_BYTES = 512 * 1024
_MAX_COMMIT_MESSAGE = 4096
_MAX_MR_TITLE = 255
_MAX_MR_TITLE_INPUT = 1024
_MAX_MR_DESCRIPTION = 64 * 1024
_DUPLICATE_MR_MESSAGE = "another open merge request already exists"


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
    value = value.strip()
    if (not value and not allow_empty) or len(value) > maximum or "\x00" in value:
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


def _same_origin_url(value: Any, origin: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_PROJECT_REFERENCE:
        raise GitLabError("invalid_remote_data")
    parsed = urlsplit(value)
    configured = urlsplit(origin)
    if (
        parsed.scheme != configured.scheme
        or parsed.hostname != configured.hostname
        or parsed.port != configured.port
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
    def __init__(self, client: GitLabClient) -> None:
        self.client = client

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

    def resolve_project(self, reference: str | int) -> dict[str, Any]:
        deadline = self.client.operation_deadline()
        parsed = self._parse_project_reference(reference)
        endpoint = _project_endpoint(parsed["project"])
        payload = _as_object(
            self.client.get_json(f"/api/v4/projects/{endpoint}", deadline=deadline)
        )
        project_id = payload.get("id")
        slug = payload.get("path_with_namespace")
        name = payload.get("name")
        web_url = payload.get("web_url")
        if (
            isinstance(project_id, bool)
            or not isinstance(project_id, int)
            or project_id <= 0
            or not isinstance(slug, str)
            or "/" not in slug
            or len(slug) > _MAX_PROJECT_SLUG
            or not isinstance(name, str)
            or len(name) > 512
        ):
            raise GitLabError("invalid_remote_data")
        _same_origin_url(web_url, self.client.auth.origin)
        if any(part in {"", ".", ".."} for part in slug.split("/")):
            raise GitLabError("invalid_remote_data")
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
    ) -> PageResult:
        deadline = self.client.operation_deadline()
        page = 1
        items: list[dict[str, Any]] = []
        truncated = False
        next_page: int | None = None
        next_offset: int | None = None
        while page <= self.client.max_pages:
            query = dict(params)
            query.update({"per_page": 100, "page": page})
            payload, headers = self.client.get_json_page(
                path, params=query, deadline=deadline
            )
            values = _as_list(payload)
            for offset, raw in enumerate(values):
                if len(items) >= max_items:
                    truncated = True
                    next_page = page
                    next_offset = offset
                    break
                items.append(normalize(_as_object(raw)))
            if truncated:
                break
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
            if candidate > self.client.max_pages:
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

        def normalize(item: Mapping[str, Any]) -> dict[str, Any]:
            identifier = item.get("id")
            if isinstance(identifier, bool) or not isinstance(identifier, int):
                raise GitLabError("invalid_remote_data")
            projected: dict[str, Any] = {"id": identifier}
            iid = item.get("iid")
            if isinstance(iid, int) and not isinstance(iid, bool):
                projected["iid"] = iid
            for field in (
                "ref",
                "sha",
                "status",
                "source",
                "web_url",
                "created_at",
                "updated_at",
            ):
                value = item.get(field)
                if value is not None and (
                    not isinstance(value, str) or len(value) > _MAX_PROJECT_REFERENCE
                ):
                    raise GitLabError("invalid_remote_data")
                projected[field] = value
            if projected.get("web_url") is not None:
                _same_origin_url(projected["web_url"], self.client.auth.origin)
            return projected

        pages = self._paginate(
            f"/api/v4/projects/{project_endpoint}/pipelines",
            params=params,
            max_items=max_items,
            normalize=normalize,
        )
        return {
            "project": str(project),
            "pipelines": list(pages.items),
            "count": len(pages.items),
            "truncated": pages.truncated,
            "continuation": self._continuation(pages),
        }

    def _write_json(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any],
        *,
        deadline: float,
    ) -> tuple[int, Any]:
        """Perform exactly one bounded mutating request without retrying."""

        self.client._validate_path(path)
        self.client._check_cancelled(deadline)
        try:
            with self.client._client.stream(
                method,
                path,
                json=dict(payload),
                timeout=self.client._request_timeout(deadline),
            ) as response:
                if 300 <= response.status_code < 400:
                    raise GitLabError("invalid_remote_data")
                body = bytearray()
                for chunk in response.iter_bytes():
                    self.client._check_cancelled(deadline)
                    if len(body) + len(chunk) > self.client.max_response_bytes:
                        raise GitLabError("capacity")
                    body.extend(chunk)
                if response.status_code >= 400 and response.status_code not in {
                    400,
                    409,
                }:
                    raise self.client._error_for_status(response.status_code)
                try:
                    decoded = json.loads(bytes(body))
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    if response.status_code in {400, 409}:
                        decoded = None
                    else:
                        raise GitLabError("invalid_remote_data") from None
                return response.status_code, decoded
        except GitLabError:
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            self.client._check_cancelled(deadline)
            raise GitLabError("transient") from None

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
            _as_object(reconciled), project_result=project_result, branch=branch
        )
        self._require_reconciled_identity(partial_identity, reconciled_identity)
        return result

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
        self._require_reconciled_identity(partial_identity, reconciled_identity)
        return result

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
        self._require_reconciled_identity(partial_identity, reconciled_identity)
        return result

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
