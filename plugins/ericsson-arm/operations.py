"""Bounded, redacted Artifactory operations.

The endpoint set follows super-cli's internal/arm. The operational
behaviour -- checksum-first deploy with a fallback, AQL include rules,
folder delete semantics -- follows the OSCAR shell scripts in
oscar_app/oscar/utils, which have production knowledge super-cli does not.
Redaction and approval discipline follow ericsson-jira.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from typing import Any, BinaryIO, Mapping
from urllib.parse import unquote_to_bytes

if __package__:
    from ._common.envelope import result_envelope
    from ._common.errors import ConnectorError
    from ._common.guardrails import require_explicit_intent
    from .aql import prepare as prepare_aql
    from .models import ArmError
else:
    from _common.envelope import result_envelope
    from _common.errors import ConnectorError
    from _common.guardrails import require_explicit_intent
    from aql import prepare as prepare_aql
    from models import ArmError


_REPO_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_PATH_CHARS = 1024
_MAX_CHILDREN = 1000
_MAX_PROPERTY_KEYS = 64
_MAX_PROPERTY_VALUES = 32
_MAX_PROPERTY_CHARS = 1024
_CHECKSUM_CHUNK = 1024 * 1024
_MAX_PATH_DECODE_PASSES = 4
_ENCODED_PATH_ROUTING_DELIMITER = re.compile(r"%(?:2f|3b|5c)", re.IGNORECASE)


def _bounded_string(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value[:maximum]


def _as_int(value: Any) -> int | None:
    """Artifactory sends size as a JSON string. Coerce, or report nothing."""
    if type(value) is int:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _has_unsafe_url_routing(value: str) -> bool:
    """Detect encoded values that URL parsing could turn into routing syntax.

    The shared transport decodes paths up to this same bound. Rejecting query
    syntax, matrix parameters, and encoded separators here keeps a delete
    approval's path from being reinterpreted when the request URL is built.
    """
    current = value
    for _ in range(_MAX_PATH_DECODE_PASSES):
        if (
            "?" in current
            or "#" in current
            or ";" in current
            or _ENCODED_PATH_ROUTING_DELIMITER.search(current) is not None
        ):
            return True
        try:
            decoded = unquote_to_bytes(current).decode("utf-8")
        except UnicodeDecodeError:
            # The transport rejects invalid URL encoding before any request.
            return False
        if decoded == current:
            return False
        current = decoded
    return (
        "?" in current
        or "#" in current
        or ";" in current
        or _ENCODED_PATH_ROUTING_DELIMITER.search(current) is not None
    )


class _BoundedUpload:
    """Read no more than the already-hashed bytes from one file descriptor."""

    def __init__(self, handle: BinaryIO, size: int) -> None:
        self._handle = handle
        self._remaining = size

    def read(self, size: int = -1) -> bytes:
        if self._remaining == 0:
            return b""
        wanted = self._remaining if size is None or size < 0 else min(size, self._remaining)
        block = self._handle.read(wanted)
        if not block:
            raise ArmError("invalid_input")
        self._remaining -= len(block)
        return block

    def __iter__(self):
        while self._remaining:
            yield self.read(min(_CHECKSUM_CHUNK, self._remaining))


class ArmOperations:
    def __init__(self, client, *, max_pages: int = 1) -> None:
        if type(max_pages) is not int or not 1 <= max_pages <= 10:
            raise ArmError("invalid_configuration")
        self.client = client
        self.max_pages = max_pages
        self.base = client.path_prefix.rstrip("/")

    def _redact(self, value: str | None) -> str | None:
        """Strip configured credentials from every remote text value."""
        if value is None:
            return None
        auth = self.client.auth
        for secret in (
            getattr(auth, "auth_header_value", ""),
            getattr(auth, "token", ""),
        ):
            if isinstance(secret, str) and secret:
                value = value.replace(secret, "<redacted>")
        return value

    def _remote_string(self, value: Any, maximum: int) -> str | None:
        """Bound and redact one string copied from an Artifactory response."""
        return _bounded_string(
            self._redact(value) if isinstance(value, str) else None,
            maximum,
        )

    @staticmethod
    def _repo(value: Any) -> str:
        if not isinstance(value, str) or _REPO_KEY.fullmatch(value) is None:
            raise ArmError(
                "invalid_input",
                remediation="Repository must be a single Artifactory repository key.",
            )
        return value

    @staticmethod
    def _path(value: Any, *, allow_empty: bool = False) -> str:
        """Normalise a path while refusing confinement-escaping values."""
        if not isinstance(value, str) or len(value) > _MAX_PATH_CHARS:
            raise ArmError("invalid_input")
        cleaned = value.strip().strip("/")
        if not cleaned:
            if allow_empty:
                return ""
            raise ArmError("invalid_input")
        if (
            "\x00" in cleaned
            or "\\" in cleaned
            or ".." in cleaned.split("/")
            or _has_unsafe_url_routing(cleaned)
            or any(character.isspace() for character in cleaned)
        ):
            raise ArmError(
                "invalid_input",
                remediation="Path must be a plain repository path with no '..' segments.",
            )
        return cleaned

    @staticmethod
    def _bounded_max(value: Any, maximum: int) -> int:
        if type(value) is not int or not 1 <= value <= maximum:
            raise ArmError("invalid_input")
        return value

    @staticmethod
    def _mapping(payload: Any) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping):
            raise ArmError("invalid_remote_data")
        return payload

    def _storage_path(self, repo: str, path: str) -> str:
        suffix = f"/{path}" if path else ""
        return f"{self.base}/api/storage/{repo}{suffix}"

    def list_repositories(
        self,
        *,
        repository_type: str | None = None,
        package_type: str | None = None,
        max_results: int = 25,
    ) -> dict[str, Any]:
        """Enumerate visible repositories, optionally filtered.

        Invariant: every remote string flows through ``self._redact`` via
        ``self._remote_string`` before bounding or returning it.
        """
        max_results = self._bounded_max(max_results, 100)
        params: dict[str, Any] = {}
        for name, value in (("type", repository_type),
                            ("packageType", package_type)):
            if value is None:
                continue
            if not isinstance(value, str) or not 1 <= len(value) <= 64:
                raise ArmError("invalid_input")
            params[name] = value

        payload = self.client.get_json(f"{self.base}/api/repositories", params=params)
        if not isinstance(payload, list):
            raise ArmError("invalid_remote_data")

        rows = [row for row in payload if isinstance(row, Mapping)]
        items = [
            {
                "key": self._remote_string(row.get("key"), 128) or "",
                "type": self._remote_string(row.get("type"), 64) or "",
                "package_type": self._remote_string(row.get("packageType"), 64) or "",
                "description": self._remote_string(row.get("description"), 512) or "",
                "url": self._remote_string(row.get("url"), 2048) or "",
            }
            for row in rows[:max_results]
        ]
        truncated = len(rows) > max_results
        return result_envelope(
            items,
            total=len(rows),
            truncated=truncated,
            hint=(
                "More repositories exist. Raise max_results, or filter with "
                "repository_type or package_type." if truncated else None
            ),
        )

    def artifact_info(
        self, repo: str, path: str, *, max_children: int = 100
    ) -> dict[str, Any]:
        """Fetch one artefact's or folder's metadata from one endpoint."""
        repo = self._repo(repo)
        path = self._path(path, allow_empty=True)
        max_children = self._bounded_max(max_children, _MAX_CHILDREN)

        payload = self._mapping(self.client.get_json(self._storage_path(repo, path)))
        raw_children = payload.get("children")
        is_folder = isinstance(raw_children, list)

        children: list[dict[str, Any]] = []
        children_truncated = False
        if is_folder:
            entries = [child for child in raw_children if isinstance(child, Mapping)]
            children_truncated = len(entries) > max_children
            for child in entries[:max_children]:
                uri = self._remote_string(child.get("uri"), 1024) or ""
                children.append({
                    "name": uri.lstrip("/"),
                    "folder": child.get("folder") is True,
                })

        checksums = payload.get("checksums")
        return {
            "repo": self._remote_string(payload.get("repo"), 128) or self._redact(repo),
            "path": self._remote_string(payload.get("path"), _MAX_PATH_CHARS) or self._redact(f"/{path}"),
            "kind": "folder" if is_folder else "file",
            "size": None if is_folder else _as_int(payload.get("size")),
            "mime_type": self._remote_string(payload.get("mimeType"), 255),
            "created": self._remote_string(payload.get("created"), 64),
            "modified": self._remote_string(payload.get("lastModified"), 64),
            "download_uri": self._remote_string(payload.get("downloadUri"), 2048),
            "checksums": {
                name: self._remote_string(checksums.get(name), 128) or ""
                for name in ("md5", "sha1", "sha256")
            } if isinstance(checksums, Mapping) else {},
            "children": children,
            "children_truncated": children_truncated,
        }

    def search_artifacts(
        self, query: str, *, max_results: int = 25
    ) -> dict[str, Any]:
        """Search artefacts with bounded Artifactory Query Language.

        Invariant: every remote string flows through ``self._redact`` via
        ``self._remote_string`` before bounding or returning it.
        """
        max_results = self._bounded_max(max_results, 100)
        prepared = prepare_aql(query, max_results=max_results)

        payload = self._mapping(
            self.client.post_text(f"{self.base}/api/search/aql", prepared)
        )
        results = payload.get("results")
        if not isinstance(results, list):
            raise ArmError("invalid_remote_data")

        rows = [row for row in results if isinstance(row, Mapping)]
        items = []
        for row in rows[:max_results]:
            repo = row.get("repo")
            path = row.get("path")
            name = row.get("name")
            joined = "/".join(
                part
                for part in (repo, path, name)
                if isinstance(part, str) and part and part != "."
            )
            items.append({
                "repo": self._remote_string(repo, 128) or "",
                "path": self._remote_string(path, _MAX_PATH_CHARS) or "",
                "name": self._remote_string(name, 512) or "",
                "full_path": self._remote_string(joined, 2048) or "",
                "size": _as_int(row.get("size")),
                "created": self._remote_string(row.get("created"), 64),
                "modified": self._remote_string(row.get("modified"), 64),
            })

        # AQL's range.total counts the rows this response carried, not the
        # rows that matched, so it is deliberately not reported as `total`.
        # A full page is the only truncation signal available.
        truncated = len(results) >= max_results
        return result_envelope(
            items,
            truncated=truncated,
            hint=(
                "The result set filled max_results, so more artefacts may "
                "match. Raise max_results or narrow the query."
                if truncated else None
            ),
        )

    def get_properties(
        self, repo: str, path: str, *, keys: list[str] | None = None
    ) -> dict[str, Any]:
        """Read an artefact's Artifactory properties.

        This is the join key back to GitLab: CI stamps build.number,
        build.name and vcs.revision here, so properties are what connect a
        deployed artefact to the pipeline and commit that produced it.

        Read-only by design. Properties drive promotion gates, so a write
        here could promote an artefact that has not passed them.

        Invariant: every remote property string flows through ``self._redact``
        via ``self._remote_string`` before bounding or returning it.
        """
        repo = self._repo(repo)
        path = self._path(path)

        selector = ""
        if keys is not None:
            if (
                not isinstance(keys, list)
                or not 1 <= len(keys) <= _MAX_PROPERTY_KEYS
                or any(
                    not isinstance(key, str)
                    or not key.strip()
                    or "," in key
                    or ";" in key
                    or len(key) > 255
                    for key in keys
                )
            ):
                raise ArmError(
                    "invalid_input",
                    remediation=(
                        "keys must be a non-empty list of property names "
                        "containing no commas or semicolons."
                    ),
                )
            # A comma inside a key would silently become two keys, so keys
            # are refused above rather than escaped.
            selector = ",".join(keys)

        payload = self._mapping(
            self.client.get_json(
                self._storage_path(repo, path), params={"properties": selector}
            )
        )
        raw = payload.get("properties")
        if raw is None:
            properties: dict[str, list[str]] = {}
        elif isinstance(raw, Mapping):
            properties = {
                self._remote_string(name, 255) or "": [
                    self._remote_string(value, _MAX_PROPERTY_CHARS) or ""
                    for value in values[:_MAX_PROPERTY_VALUES]
                ]
                for name, values in raw.items()
                if isinstance(values, list)
            }
        else:
            raise ArmError("invalid_remote_data")

        return {
            "repo": repo,
            "path": path,
            "properties": properties,
            "count": len(properties),
        }

    @staticmethod
    def _supports_secure_open() -> bool:
        return (
            os.open in os.supports_dir_fd
            and hasattr(os, "O_DIRECTORY")
            and hasattr(os, "O_NOFOLLOW")
        )

    @staticmethod
    def _open_from_anchor(real: str, root: str) -> int:
        """Traverse from filesystem root with no-follow directory descriptors."""
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        current = os.open("/", directory_flags)
        try:
            root_parts = [] if root == "/" else os.path.relpath(root, "/").split(os.sep)
            for component in root_parts:
                next_fd = os.open(component, directory_flags, dir_fd=current)
                os.close(current)
                current = next_fd

            relative = os.path.relpath(real, root)
            parts = relative.split(os.sep)
            if not parts or parts == ["."] or any(part in {"", ".", ".."} for part in parts):
                raise OSError("source is not below its open anchor")
            for component in parts[:-1]:
                next_fd = os.open(component, directory_flags, dir_fd=current)
                os.close(current)
                current = next_fd
            return os.open(
                parts[-1],
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                dir_fd=current,
            )
        finally:
            os.close(current)

    @staticmethod
    def _open_path_fallback(real: str) -> int:
        """Fallback retains final-component no-follow where dir_fd is absent."""
        return os.open(
            real,
            os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0),
        )

    def _open_source(self, source_file: Any) -> tuple[BinaryIO, str, int]:
        """Open one validated regular file descriptor for hashing and upload."""
        if not isinstance(source_file, str) or not source_file:
            raise ArmError("invalid_input")
        if not os.path.isabs(source_file):
            raise ArmError(
                "invalid_input",
                remediation="source_file must be an absolute path.",
            )
        real = os.path.realpath(source_file)
        root = getattr(self.client.auth, "deploy_root", None)
        root_real = None
        if root:
            try:
                root_real = os.path.realpath(root)
                contained = os.path.commonpath([real, root_real]) == root_real
            except ValueError:
                contained = False
            if not contained:
                raise ArmError(
                    "permission",
                    remediation=(
                        "This profile confines uploads to its configured deploy source "
                        "root."
                    ),
                )
            if not self._supports_secure_open():
                raise ArmError("invalid_configuration")
        try:
            expected = os.stat(real)
        except OSError:
            raise ArmError(
                "not_found",
                remediation="source_file does not name a readable file.",
            ) from None
        if not stat.S_ISREG(expected.st_mode):
            raise ArmError(
                "not_found",
                remediation="source_file does not name a readable file.",
            )

        fd = None
        try:
            anchor = root_real if root_real is not None else "/"
            fd = (
                self._open_from_anchor(real, anchor)
                if self._supports_secure_open()
                else self._open_path_fallback(real)
            )
            opened = os.fstat(fd)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino)
            ):
                raise ArmError("invalid_input")
            size = opened.st_size
            handle = os.fdopen(fd, "rb")
            fd = None
        except OSError:
            raise ArmError("invalid_input") from None
        finally:
            if fd is not None:
                os.close(fd)

        limit = getattr(self.client.auth, "max_deploy_bytes", 0)
        if size > limit:
            handle.close()
            raise ArmError(
                "capacity",
                remediation=(
                    "The file is larger than this profile's maximum upload size. "
                    "Raise it in the profile if this is expected."
                ),
            )
        return handle, real, size

    @staticmethod
    def _file_checksums(handle: BinaryIO, maximum_bytes: int) -> tuple[dict[str, str], int]:
        """Compute Artifactory's three checksums in a single file pass."""
        digests = {
            "md5": hashlib.md5(usedforsecurity=False),
            "sha1": hashlib.sha1(usedforsecurity=False),
            "sha256": hashlib.sha256(),
        }
        size = 0
        for block in iter(lambda: handle.read(_CHECKSUM_CHUNK), b""):
            size += len(block)
            if size > maximum_bytes:
                raise ArmError("capacity")
            for digest in digests.values():
                digest.update(block)
        return {name: digest.hexdigest() for name, digest in digests.items()}, size

    def _verify_deploy(self, response, checksums: dict[str, str]) -> Mapping[str, Any]:
        """Verify that Artifactory's deploy response names this exact file."""
        try:
            payload = json.loads(response.body) if response.body else None
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            payload = None
        if not isinstance(payload, Mapping):
            raise ArmError(
                "invalid_remote_data",
                remediation="Artifactory did not return a deploy result.",
            )
        reported = payload.get("checksums")
        if not isinstance(reported, Mapping) or not reported.get("sha256"):
            raise ArmError(
                "invalid_remote_data",
                remediation="Artifactory returned no checksums to verify against.",
            )
        if reported.get("sha256") != checksums["sha256"]:
            raise ArmError(
                "invalid_remote_data",
                remediation=(
                    "The sha256 checksum Artifactory reported does not match the "
                    "file that was sent. Do not treat this artefact as published."
                ),
            )
        return payload

    def deploy(
        self,
        repo: str,
        path: str,
        source_file: str,
        *,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Publish one local file with a checksum-only probe and full fallback."""
        repo = self._repo(repo)
        path = self._path(path)
        handle, real_path, size = self._open_source(source_file)
        try:
            checksums, size = self._file_checksums(
                handle, getattr(self.client.auth, "max_deploy_bytes", 0)
            )
            try:
                execute = require_explicit_intent(
                    dry_run=dry_run,
                    confirm=confirm,
                    action=f"an upload to {repo}/{path}",
                )
            except ConnectorError as exc:
                raise ArmError(exc.category) from None

            if not execute:
                return {
                    "ok": True,
                    "dry_run": True,
                    "repo": repo,
                    "path": path,
                    "source_file": real_path,
                    "size": size,
                    "checksums": checksums,
                    "deduplicated": None,
                    "bytes_uploaded": None,
                }

            target = f"{self.base}/{repo}/{path}"
            checksum_headers = {
                "X-Checksum-Sha256": checksums["sha256"],
                "X-Checksum-Sha1": checksums["sha1"],
                "X-Checksum-Md5": checksums["md5"],
            }
            probe = self.client.checksum_probe(
                target,
                extra_headers={"X-Checksum-Deploy": "true", **checksum_headers},
            )
            if 200 <= probe.status < 300:
                payload = self._verify_deploy(probe, checksums)
                return {
                    "ok": True,
                    "dry_run": False,
                    "repo": repo,
                    "path": path,
                    "source_file": real_path,
                    "size": size,
                    "checksums": checksums,
                    "deduplicated": True,
                    "bytes_uploaded": 0,
                    "download_uri": self._remote_string(payload.get("downloadUri"), 2048),
                }

            handle.seek(0)
            response = self.client.send(
                "PUT",
                target,
                extra_headers=checksum_headers,
                content=_BoundedUpload(handle, size),
            )
            payload = self._verify_deploy(response, checksums)
            return {
                "ok": True,
                "dry_run": False,
                "repo": repo,
                "path": path,
                "source_file": real_path,
                "size": size,
                "checksums": checksums,
                "deduplicated": False,
                "bytes_uploaded": size,
                "download_uri": self._remote_string(payload.get("downloadUri"), 2048),
            }
        finally:
            handle.close()

    def delete(
        self,
        repo: str,
        path: str,
        *,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Delete one Artifactory path with a read-only preview option.

        A folder is deleted recursively by Artifactory in this one request;
        this connector must never turn one approval into a per-child loop.
        """
        repo = self._repo(repo)
        # Empty paths name the repository root and are never deletable.
        path = self._path(path)
        try:
            execute = require_explicit_intent(
                dry_run=dry_run,
                confirm=confirm,
                action=f"deletion of {repo}/{path}",
            )
        except ConnectorError as exc:
            raise ArmError(exc.category) from None

        if not execute:
            try:
                preview = self.artifact_info(repo, path, max_children=_MAX_CHILDREN)
            except ArmError as exc:
                if exc.category != "not_found":
                    raise
                return {
                    "ok": True,
                    "dry_run": True,
                    "repo": repo,
                    "path": path,
                    "exists": False,
                    "deleted": False,
                }
            return {
                "ok": True,
                "dry_run": True,
                "repo": repo,
                "path": path,
                "exists": True,
                "deleted": False,
                "kind": preview["kind"],
                "size": preview["size"],
                "child_count": len(preview["children"]),
                "child_count_truncated": preview["children_truncated"],
            }

        response = self.client.send(
            "DELETE", f"{self.base}/{repo}/{path}", classify=False
        )
        if response.status == 404:
            return {
                "ok": True,
                "dry_run": False,
                "repo": repo,
                "path": path,
                "deleted": True,
                "already_absent": True,
            }
        if not 200 <= response.status < 300:
            self.client._classify(response)
        return {
            "ok": True,
            "dry_run": False,
            "repo": repo,
            "path": path,
            "deleted": True,
            "already_absent": False,
        }
