"""Connector operations composed from SharePoint and generic Graph clients."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import fnmatch
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

from .auth import build_identity_config
from .client import SharePointClient
from .models import (
    SharePointAmbiguousWriteError,
    SharePointAuditError,
    SharePointCancelledError,
    SharePointConfiguration,
    SharePointDeadlineError,
    SharePointFileBoundaryError,
    SharePointResolutionError,
    SharePointWriteError,
)
from . import audit as sharepoint_audit


_INVALID_FILENAME_CHARACTERS = frozenset('<>:"/\\|?*')
_WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)
_COPY_MONITOR_PATH = re.compile(
    r"^/(?:(?:sites|teams)/[^/]+/)?_api/v2\.[01]/monitor/[A-Za-z0-9._~-]+/?$"
)


class OneOperationFileAuthorization:
    """Host-supplied, single-consumption expansion of the download boundary."""

    def __init__(self, *, tool_name: str, root: Path | str, interactive: bool) -> None:
        self.tool_name = str(tool_name)
        self.root = Path(root).expanduser().resolve(strict=False)
        self.interactive = interactive is True
        self._consumed = False
        self._lock = threading.Lock()

    def consume(self, *, tool_name: str, destination: Path, unattended: bool) -> Path:
        with self._lock:
            if unattended:
                raise SharePointFileBoundaryError(
                    "unattended operations cannot expand the configured file boundary"
                )
            if self._consumed:
                raise SharePointFileBoundaryError(
                    "file authorization was already consumed"
                )
            if not self.interactive or self.tool_name != tool_name:
                raise SharePointFileBoundaryError(
                    "file authorization is not valid for this operation"
                )
            if not _is_within(destination, self.root):
                raise SharePointFileBoundaryError(
                    "destination is outside the authorized root"
                )
            self._consumed = True
            return self.root


def _control(
    *,
    deadline: float | None,
    cancel_check,
    clock=time.monotonic,
) -> None:
    if cancel_check is not None and cancel_check():
        raise SharePointCancelledError("SharePoint operation was cancelled")
    if deadline is not None and clock() >= deadline:
        raise SharePointDeadlineError("SharePoint operation deadline exceeded")


def _translated_graph_control(error: Exception) -> RuntimeError | None:
    try:
        from tools.microsoft_graph_client import (
            MicrosoftGraphCancelledError,
            MicrosoftGraphDeadlineError,
        )
    except ImportError:
        return None
    if isinstance(error, MicrosoftGraphCancelledError):
        return SharePointCancelledError("SharePoint operation was cancelled")
    if isinstance(error, MicrosoftGraphDeadlineError):
        return SharePointDeadlineError("SharePoint operation deadline exceeded")
    return None


def _bounded_text(value: Any, label: str, maximum: int = 4096) -> str:
    text = str(value or "")
    if len(text) > maximum or any(ord(character) < 32 for character in text):
        raise SharePointResolutionError(f"Graph returned invalid {label}")
    return text


def _safe_remote_web_url(value: Any, tenant_hosts) -> str:
    if value in {None, ""}:
        return ""
    text = _bounded_text(value, "web URL", 8192)
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise SharePointResolutionError("Graph returned invalid web URL") from None
    if (
        parts.scheme != "https"
        or (parts.hostname or "").lower().rstrip(".") not in tenant_hosts
        or parts.username is not None
        or parts.password is not None
        or port not in {None, 443}
    ):
        raise SharePointResolutionError(
            "Graph result escaped configured tenant authority"
        )
    return text


def _list_row(
    raw: Any, *, drive_id: str, parent_path: str, tenant_hosts
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise SharePointResolutionError("Graph returned invalid DriveItem metadata")
    item_id = _bounded_text(raw.get("id"), "item id", 1024)
    name = _bounded_text(raw.get("name"), "item name", 1024)
    if not item_id or not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise SharePointResolutionError("Graph returned invalid DriveItem identity")
    folder = raw.get("folder")
    file_facet = raw.get("file")
    kind = (
        "folder"
        if isinstance(folder, Mapping)
        else "file"
        if isinstance(file_facet, Mapping)
        else "unknown"
    )
    size = raw.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        size = 0
    relative_path = f"{parent_path}/{name}" if parent_path else name
    return {
        "id": item_id,
        "drive_id": drive_id,
        "name": name,
        "relative_path": relative_path,
        "kind": kind,
        "size": size,
        "mime_type": _bounded_text(
            file_facet.get("mimeType") if isinstance(file_facet, Mapping) else "",
            "MIME type",
            512,
        ),
        "web_url": _safe_remote_web_url(raw.get("webUrl"), tenant_hosts),
        "modified": _bounded_text(
            raw.get("lastModifiedDateTime"), "modified time", 128
        ),
    }


async def list_items_with_client(
    client: SharePointClient,
    *,
    url: str | None = None,
    drive_id: str | None = None,
    item_id: str | None = None,
    recursive: bool,
    max_pages: int,
    max_items: int,
    max_depth: int,
    max_metadata_bytes: int,
    name_patterns: tuple[str, ...] = (),
    extensions: tuple[str, ...] = (),
    deadline: float | None = None,
    cancel_check=None,
    clock=time.monotonic,
) -> dict[str, Any]:
    _control(deadline=deadline, cancel_check=cancel_check, clock=clock)
    resolved = await client.get_item(
        url=url,
        drive_id=drive_id,
        item_id=item_id,
        deadline=deadline,
        cancel_check=cancel_check,
    )
    root = resolved["item"]
    if root.get("kind") != "folder":
        raise SharePointResolutionError("listing requires a folder DriveItem")
    safe_drive = _bounded_text(resolved["drive"].get("id"), "drive id", 1024)
    root_id = _bounded_text(root.get("id"), "item id", 1024)
    if not safe_drive or not root_id:
        raise SharePointResolutionError("resolved folder identity is incomplete")

    page_limit = max(1, min(int(max_pages), 1000))
    item_limit = max(1, min(int(max_items), 100_000))
    depth_limit = max(0, min(int(max_depth), 64))
    byte_limit = max(1, min(int(max_metadata_bytes), 64 * 1024 * 1024))
    patterns = tuple(
        str(pattern).strip().casefold()
        for pattern in name_patterns
        if str(pattern).strip()
    )
    suffixes = tuple(
        str(extension).strip().casefold().lstrip(".")
        for extension in extensions
        if str(extension).strip().lstrip(".")
    )
    if (
        len(patterns) > 64
        or len(suffixes) > 64
        or any(len(value) > 256 for value in (*patterns, *suffixes))
    ):
        raise SharePointResolutionError("listing filters exceed supported bounds")
    queue: list[tuple[str, str, int]] = [(root_id, "", 0)]
    visited = {root_id}
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    pages = 0
    metadata_bytes = 0
    examined_items = 0
    truncated = False

    def warn(message: str) -> None:
        nonlocal truncated
        truncated = True
        if message not in warnings:
            warnings.append(message)

    while queue:
        if pages >= page_limit:
            warn("page limit reached")
            break
        folder_id, parent_path, depth = queue.pop(0)
        endpoint = f"/drives/{safe_drive}/items/{folder_id}/children"
        async for page in client.graph.iterate_pages(
            endpoint,
            params={
                "$select": "id,name,size,folder,file,webUrl,lastModifiedDateTime,parentReference",
                "$top": min(200, item_limit),
            },
            deadline=deadline,
            cancel_check=cancel_check,
        ):
            _control(deadline=deadline, cancel_check=cancel_check, clock=clock)
            pages += 1
            if pages > page_limit:
                warn("page limit reached")
                queue.clear()
                break
            if not isinstance(page, Mapping) or not isinstance(page.get("value"), list):
                raise SharePointResolutionError("Graph returned invalid folder page")
            stop = False
            for raw in page["value"]:
                _control(deadline=deadline, cancel_check=cancel_check, clock=clock)
                if examined_items >= item_limit:
                    warn("item limit reached")
                    stop = True
                    break
                examined_items += 1
                row = _list_row(
                    raw,
                    drive_id=safe_drive,
                    parent_path=parent_path,
                    tenant_hosts=client.tenant_hosts,
                )
                include = True
                if patterns:
                    include = any(
                        fnmatch.fnmatchcase(
                            (
                                row["relative_path"] if "/" in pattern else row["name"]
                            ).casefold(),
                            pattern,
                        )
                        for pattern in patterns
                    )
                elif suffixes:
                    include = (
                        row["kind"] == "file"
                        and "." in row["name"]
                        and row["name"].rsplit(".", 1)[-1].casefold() in suffixes
                    )
                if include:
                    encoded_size = len(
                        json.dumps(
                            row, ensure_ascii=False, separators=(",", ":")
                        ).encode("utf-8")
                    )
                    if metadata_bytes + encoded_size > byte_limit:
                        warn("metadata byte limit reached")
                        stop = True
                        break
                    rows.append(row)
                    metadata_bytes += encoded_size
                if recursive and row["kind"] == "folder":
                    if row["id"] in visited:
                        warn("cycle skipped")
                    elif depth >= depth_limit:
                        warn("maximum recursion depth reached")
                    else:
                        visited.add(row["id"])
                        queue.append((row["id"], row["relative_path"], depth + 1))
            if stop:
                queue.clear()
                break
            if pages >= page_limit and page.get("@odata.nextLink"):
                warn("page limit reached")
                queue.clear()
                break
        if truncated and not queue:
            break
    return {
        "status": "truncated" if truncated else "complete",
        "truncated": truncated,
        "warnings": warnings,
        "items": rows,
        "counts": {
            "items": len(rows),
            "pages": min(pages, page_limit),
            "metadata_bytes": metadata_bytes,
        },
    }


def _safe_filename(value: str, fallback: str) -> str:
    normalized = "".join(
        "_"
        if character in _INVALID_FILENAME_CHARACTERS or ord(character) < 32
        else character
        for character in str(value or "")
    ).strip(" .")
    if normalized in {"", ".", ".."}:
        normalized = fallback
    if normalized.rsplit(".", 1)[0].casefold() in _WINDOWS_DEVICE_NAMES:
        normalized = f"_{normalized}"
    encoded = normalized.encode("utf-8")
    while len(encoded) > 240:
        normalized = normalized[:-1]
        encoded = normalized.encode("utf-8")
    return normalized or fallback


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise SharePointFileBoundaryError("local path crosses a symbolic link")


def _prepare_directory(path: Path) -> None:
    _reject_symlink_components(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _reject_symlink_components(path)
    if not path.is_dir():
        raise SharePointFileBoundaryError("local path parent is not a directory")


def _download_target(
    config: SharePointConfiguration,
    *,
    item_name: str,
    item_id: str,
    destination: str | None,
    file_authorization: OneOperationFileAuthorization | None,
    unattended: bool,
) -> tuple[Path, Path, str]:
    configured_root = config.download_root.resolve(strict=False)
    _reject_symlink_components(config.download_root)
    safe_name = _safe_filename(item_name, f"item-{item_id}")
    if destination:
        raw = Path(destination).expanduser()
        if not raw.is_absolute() and ".." in raw.parts:
            raise SharePointFileBoundaryError("destination traversal is not allowed")
        candidate = raw if raw.is_absolute() else configured_root / raw
        if candidate.exists() and candidate.is_dir():
            candidate = candidate / safe_name
    else:
        candidate = configured_root / safe_name
    _reject_symlink_components(candidate)
    target = candidate.resolve(strict=False)
    if _is_within(target, configured_root):
        return target, configured_root, "configured_download_root"
    if file_authorization is None:
        raise SharePointFileBoundaryError(
            "destination is outside the configured download root"
        )
    external_root = file_authorization.consume(
        tool_name="sharepoint_download", destination=target, unattended=unattended
    )
    _reject_symlink_components(external_root)
    return target, external_root, "interactive_authorization"


async def download_with_client(
    client: SharePointClient,
    config: SharePointConfiguration,
    *,
    url: str | None = None,
    drive_id: str | None = None,
    item_id: str | None = None,
    destination: str | None = None,
    max_bytes: int | None = None,
    file_authorization: OneOperationFileAuthorization | None = None,
    unattended: bool = False,
    deadline: float | None = None,
    cancel_check=None,
) -> dict[str, Any]:
    _control(deadline=deadline, cancel_check=cancel_check)
    resolved = await client.get_item(
        url=url,
        drive_id=drive_id,
        item_id=item_id,
        deadline=deadline,
        cancel_check=cancel_check,
    )
    item = resolved["item"]
    if item.get("kind") != "file":
        raise SharePointFileBoundaryError("download target is a folder, not a file")
    safe_drive = _bounded_text(resolved["drive"].get("id"), "drive id", 1024)
    safe_item = _bounded_text(item.get("id"), "item id", 1024)
    if not safe_drive or not safe_item:
        raise SharePointResolutionError("download identity is incomplete")
    target, evidence_root, boundary = _download_target(
        config,
        item_name=_bounded_text(item.get("name"), "item name", 1024),
        item_id=safe_item,
        destination=destination,
        file_authorization=file_authorization,
        unattended=bool(unattended),
    )
    _prepare_directory(evidence_root)
    _prepare_directory(target.parent)
    if target.exists() or target.is_symlink():
        mode = target.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise SharePointFileBoundaryError("download destination is a special file")
        raise SharePointFileBoundaryError("download destination already exists")

    effective_max = config.max_bytes
    if max_bytes is not None:
        if isinstance(max_bytes, bool) or not 1 <= int(max_bytes) <= config.max_bytes:
            raise SharePointFileBoundaryError("download byte limit is invalid")
        effective_max = int(max_bytes)
    with tempfile.TemporaryDirectory(prefix=".sharepoint-", dir=target.parent) as temp:
        temporary = Path(temp) / target.name
        await client.graph.download_to_file(
            f"/drives/{safe_drive}/items/{safe_item}/content",
            temporary,
            max_bytes=effective_max,
            deadline=deadline,
            cancel_check=cancel_check,
        )
        info = temporary.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > effective_max:
            raise SharePointFileBoundaryError(
                "download did not produce a bounded regular file"
            )
        digest = hashlib.sha256()
        size = 0
        with temporary.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                size += len(chunk)
                if size > effective_max:
                    raise SharePointFileBoundaryError("download exceeded byte limit")
                digest.update(chunk)
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError:
            raise SharePointFileBoundaryError(
                "download destination already exists"
            ) from None
        try:
            target.chmod(0o600)
        except OSError:
            pass
    return {
        "item_id": safe_item,
        "drive_id": safe_drive,
        "name": target.name,
        "path": target.relative_to(evidence_root).as_posix(),
        "boundary": boundary,
        "size": size,
        "sha256": digest.hexdigest(),
    }


def client_from_configuration(configuration: Mapping[str, Any]) -> SharePointClient:
    """Build a fresh client from the host's current opaque configuration view."""
    from tools.microsoft_graph_client import MicrosoftGraphClient
    from tools.microsoft_graph_identity import create_graph_token_provider

    config = SharePointConfiguration.from_runtime(configuration)
    provider = create_graph_token_provider(
        build_identity_config(config), interactive_allowed=False
    )
    graph = MicrosoftGraphClient(provider, timeout=config.timeout_seconds)
    return SharePointClient(
        graph,
        tenant_hosts={config.tenant_host},
        max_pages=config.max_pages,
        max_items=config.max_items,
    )


async def resolve_url(
    configuration: Mapping[str, Any], url: str, *, cancel_check=None
) -> dict[str, Any]:
    config = SharePointConfiguration.from_runtime(configuration)
    return await client_from_configuration(configuration).resolve_url(
        url,
        deadline=time.monotonic() + config.timeout_seconds,
        cancel_check=cancel_check,
    )


async def get_item(
    configuration: Mapping[str, Any],
    *,
    url: str | None = None,
    drive_id: str | None = None,
    item_id: str | None = None,
    cancel_check=None,
) -> dict[str, Any]:
    config = SharePointConfiguration.from_runtime(configuration)
    return await client_from_configuration(configuration).get_item(
        url=url,
        drive_id=drive_id,
        item_id=item_id,
        deadline=time.monotonic() + config.timeout_seconds,
        cancel_check=cancel_check,
    )


async def list_items(
    configuration: Mapping[str, Any],
    *,
    url: str | None,
    drive_id: str | None,
    item_id: str | None,
    recursive: bool,
    max_pages: int | None,
    max_items: int | None,
    max_depth: int,
    max_metadata_bytes: int | None,
    name_patterns: tuple[str, ...] = (),
    extensions: tuple[str, ...] = (),
    cancel_check=None,
) -> dict[str, Any]:
    config = SharePointConfiguration.from_runtime(configuration)
    deadline = time.monotonic() + config.timeout_seconds
    return await list_items_with_client(
        client_from_configuration(configuration),
        url=url,
        drive_id=drive_id,
        item_id=item_id,
        recursive=bool(recursive),
        max_pages=min(config.max_pages, int(max_pages or config.max_pages)),
        max_items=min(config.max_items, int(max_items or config.max_items)),
        max_depth=max_depth,
        max_metadata_bytes=min(
            config.max_bytes,
            int(max_metadata_bytes or min(config.max_bytes, 1024 * 1024)),
        ),
        name_patterns=name_patterns,
        extensions=extensions,
        deadline=deadline,
        cancel_check=cancel_check,
    )


async def download(
    configuration: Mapping[str, Any],
    *,
    url: str | None,
    drive_id: str | None,
    item_id: str | None,
    destination: str | None,
    max_bytes: int | None,
    file_authorization: OneOperationFileAuthorization | None,
    unattended: bool,
    cancel_check=None,
) -> dict[str, Any]:
    config = SharePointConfiguration.from_runtime(configuration)
    return await download_with_client(
        client_from_configuration(configuration),
        config,
        url=url,
        drive_id=drive_id,
        item_id=item_id,
        destination=destination,
        max_bytes=max_bytes,
        file_authorization=file_authorization,
        unattended=unattended,
        deadline=time.monotonic() + config.timeout_seconds,
        cancel_check=cancel_check,
    )


async def list_owned_sites_with_graph(
    graph,
    *,
    tenant_hosts,
    max_pages,
    max_sites,
    max_metadata_bytes,
    deadline=None,
    cancel_check=None,
    clock=time.monotonic,
):
    _control(deadline=deadline, cancel_check=cancel_check, clock=clock)
    await graph.get_json(
        "/me",
        params={"$select": "id,displayName"},
        deadline=deadline,
        cancel_check=cancel_check,
    )
    sites = []
    warnings = []
    reasons = []
    pages = 0
    metadata_bytes = 0
    async for page in graph.iterate_pages(
        "/me/ownedObjects/microsoft.graph.group",
        params={"$select": "id,displayName", "$top": min(200, max_sites)},
        deadline=deadline,
        cancel_check=cancel_check,
    ):
        _control(deadline=deadline, cancel_check=cancel_check, clock=clock)
        if pages >= max_pages:
            reasons.append("page limit reached")
            break
        pages += 1
        values = page.get("value") if isinstance(page, Mapping) else None
        if not isinstance(values, list):
            raise SharePointResolutionError("Graph returned invalid owned-group page")
        for group in values:
            if len(sites) >= max_sites:
                reasons.append("site limit reached")
                break
            group_id = _bounded_text(
                group.get("id") if isinstance(group, Mapping) else "", "group id", 1024
            )
            if not group_id:
                continue
            try:
                raw = await graph.get_json(
                    f"/groups/{group_id}/sites/root",
                    params={
                        "$select": "id,displayName,webUrl,description,createdDateTime"
                    },
                    deadline=deadline,
                    cancel_check=cancel_check,
                )
                if not isinstance(raw, Mapping):
                    raise ValueError
                web_url = _safe_remote_web_url(raw.get("webUrl"), tenant_hosts)
                row = {
                    "id": _bounded_text(raw.get("id"), "site id", 1024),
                    "name": _bounded_text(raw.get("displayName"), "site name"),
                    "url": web_url,
                    "description": _bounded_text(raw.get("description"), "description"),
                    "created": _bounded_text(
                        raw.get("createdDateTime"), "created", 128
                    ),
                    "group_id": group_id,
                    "group_name": _bounded_text(group.get("displayName"), "group name"),
                }
            except (SharePointCancelledError, SharePointDeadlineError):
                raise
            except Exception as error:
                control_error = _translated_graph_control(error)
                if control_error is not None:
                    raise control_error from None
                warnings.append(
                    {"group_id": group_id, "category": "remote_unavailable"}
                )
                continue
            size = len(
                json.dumps(row, separators=(",", ":"), ensure_ascii=False).encode()
            )
            if metadata_bytes + size > max_metadata_bytes:
                reasons.append("metadata byte limit reached")
                break
            sites.append(row)
            metadata_bytes += size
        if reasons:
            break
    return {
        "status": "partial" if warnings else "truncated" if reasons else "complete",
        "sites": sites,
        "warnings": warnings,
        "truncated": bool(reasons),
        "truncation_reasons": list(dict.fromkeys(reasons)),
        "counts": {
            "sites": len(sites),
            "pages": pages,
            "metadata_bytes": metadata_bytes,
        },
    }


async def audit_permissions_with_browser(
    config: SharePointConfiguration,
    *,
    sites,
    selected,
    browser_profiles=None,
    browser_manager=None,
    audit_runner=sharepoint_audit.audit_sites_with_session,
    cancel_check=None,
    **limits,
):
    if browser_profiles is None or browser_manager is None:
        from tools import browser_profiles as core_profiles
        from tools import browser_session_manager as core_manager

        browser_profiles = browser_profiles or core_profiles
        browser_manager = browser_manager or core_manager
    profile = browser_profiles.get_profile(config.browser_profile)
    if (
        profile is None
        or not profile.is_enrolled
        or not browser_profiles.is_origin_trusted(profile, config.tenant_origin)
    ):
        raise SharePointAuditError(
            "named browser profile must be enrolled and trusted for tenant origin"
        )
    session = browser_manager.acquire(
        profile=config.browser_profile,
        headless=True,
        session_key=f"ericsson-sharepoint::{config.browser_profile}::audit",
        attach_global=False,
    )
    try:
        defaults = {
            "max_sites": min(config.max_items, 25),
            "max_pages_per_category": min(config.max_pages, 25),
            "max_rows_per_category": min(config.max_items, 1000),
            "max_total_rows": config.max_items,
            "max_total_bytes": config.max_bytes,
            "deadline": time.monotonic() + config.timeout_seconds,
            "cancel_check": cancel_check,
        }
        defaults.update(limits)
        return await audit_runner(
            session,
            sites=sites,
            selected=selected,
            allowed_hosts={config.tenant_host},
            **defaults,
        )
    finally:
        session.release()


def audit_ready(configuration) -> bool:
    try:
        config = SharePointConfiguration.from_runtime(configuration)
        from tools import browser_profiles

        profile = browser_profiles.get_profile(config.browser_profile)
        return bool(
            profile
            and profile.is_enrolled
            and browser_profiles.is_origin_trusted(profile, config.tenant_origin)
        )
    except Exception:
        return False


async def list_owned_sites(
    configuration,
    *,
    max_pages=None,
    max_sites=None,
    max_metadata_bytes=None,
    cancel_check=None,
):
    config = SharePointConfiguration.from_runtime(configuration)
    client = client_from_configuration(configuration)
    return await list_owned_sites_with_graph(
        client.graph,
        tenant_hosts={config.tenant_host},
        max_pages=min(config.max_pages, int(max_pages or config.max_pages)),
        max_sites=min(config.max_items, int(max_sites or min(config.max_items, 100))),
        max_metadata_bytes=min(
            config.max_bytes,
            int(max_metadata_bytes or min(config.max_bytes, 1024 * 1024)),
        ),
        deadline=time.monotonic() + config.timeout_seconds,
        cancel_check=cancel_check,
    )


async def audit_permissions(
    configuration, *, sites, selected, cancel_check=None, **limits
):
    config = SharePointConfiguration.from_runtime(configuration)
    return await audit_permissions_with_browser(
        config, sites=sites, selected=selected, cancel_check=cancel_check, **limits
    )


def _write_headers(etag: str | None) -> dict[str, str] | None:
    if etag is None:
        return None
    value = str(etag).strip()
    if not value or len(value) > 1024 or "\r" in value or "\n" in value:
        raise SharePointWriteError("ETag is invalid")
    return {"If-Match": value}


def _write_name(value: Any, label="name") -> str:
    name = str(value or "").strip()
    if (
        not name
        or len(name) > 255
        or name in {".", ".."}
        or any(c in name for c in "/\\\x00")
    ):
        raise SharePointWriteError(f"{label} is invalid")
    return name


def _validate_copy_monitor_url(location: str, tenant_host: str) -> None:
    try:
        parts = urlsplit(location)
        port = parts.port
    except (TypeError, ValueError):
        raise SharePointWriteError("copy monitor URL is invalid") from None
    if (
        parts.scheme.casefold() != "https"
        or (parts.hostname or "").casefold().rstrip(".")
        != str(tenant_host).casefold().rstrip(".")
        or parts.username is not None
        or parts.password is not None
        or port not in {None, 443}
        or bool(parts.fragment)
        or _COPY_MONITOR_PATH.fullmatch(parts.path) is None
    ):
        raise SharePointWriteError("copy monitor URL escaped tenant authority")


async def create_folder_with_client(
    client,
    *,
    parent_url,
    name,
    exist_ok=False,
    etag=None,
    deadline=None,
    cancel_check=None,
):
    parent = await client.get_item(
        url=parent_url, deadline=deadline, cancel_check=cancel_check
    )
    if parent["item"].get("kind") != "folder":
        raise SharePointWriteError("destination must be a folder")
    safe_name = _write_name(name, "folder name")
    raw = await client.graph.post_json(
        f"/drives/{parent['drive']['id']}/items/{parent['item']['id']}/children",
        json_body={
            "name": safe_name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "replace" if exist_ok else "fail",
        },
        headers=_write_headers(etag),
        retry_ambiguous=False,
        deadline=deadline,
        cancel_check=cancel_check,
    )
    return {
        "id": _bounded_text(raw.get("id"), "item id", 1024),
        "name": _bounded_text(raw.get("name"), "item name", 1024),
        "kind": "folder",
    }


async def move_item_with_client(
    client,
    *,
    source_url,
    destination_url=None,
    name=None,
    etag=None,
    deadline=None,
    cancel_check=None,
):
    source = await client.get_item(
        url=source_url, deadline=deadline, cancel_check=cancel_check
    )
    if source["item"].get("is_drive_root") is True:
        raise SharePointWriteError("source root cannot be moved")
    body = {}
    if name is not None:
        body["name"] = _write_name(name)
    if destination_url is not None:
        destination = await client.get_item(
            url=destination_url, deadline=deadline, cancel_check=cancel_check
        )
        if source["tenant_host"] != destination["tenant_host"]:
            raise SharePointWriteError("source and destination tenants differ")
        if destination["item"].get("kind") != "folder":
            raise SharePointWriteError("destination must be a folder")
        body["parentReference"] = {
            "driveId": destination["drive"]["id"],
            "id": destination["item"]["id"],
        }
    if not body:
        raise SharePointWriteError("move requires a new name or destination")
    return await client.graph.patch_json(
        f"/drives/{source['drive']['id']}/items/{source['item']['id']}",
        json_body=body,
        headers=_write_headers(etag),
        retry_ambiguous=False,
        deadline=deadline,
        cancel_check=cancel_check,
    )


async def copy_item_with_client(
    client,
    *,
    source_url,
    destination_url,
    name=None,
    max_polls=30,
    deadline=None,
    cancel_check=None,
):
    source = await client.get_item(
        url=source_url, deadline=deadline, cancel_check=cancel_check
    )
    if source["item"].get("is_drive_root") is True:
        raise SharePointWriteError("source root cannot be copied")
    destination = await client.get_item(
        url=destination_url, deadline=deadline, cancel_check=cancel_check
    )
    if source["tenant_host"] != destination["tenant_host"]:
        raise SharePointWriteError("source and destination tenants differ")
    if destination["item"].get("kind") != "folder":
        raise SharePointWriteError("destination must be a folder")
    body = {
        "parentReference": {
            "driveId": destination["drive"]["id"],
            "id": destination["item"]["id"],
        }
    }
    if name is not None:
        body["name"] = _write_name(name)
    return await client.graph.start_async_operation(
        f"/drives/{source['drive']['id']}/items/{source['item']['id']}/copy",
        json_body=body,
        max_polls=max_polls,
        deadline=deadline,
        cancel_check=cancel_check,
        monitor_url_validator=lambda location: _validate_copy_monitor_url(
            location, source["tenant_host"]
        ),
    )


async def recycle_item_with_client(
    client, *, url, etag=None, deadline=None, cancel_check=None
):
    source = await client.get_item(
        url=url, deadline=deadline, cancel_check=cancel_check
    )
    if source["item"].get("is_drive_root") is True:
        raise SharePointWriteError("library root cannot be recycled")
    await client.graph.delete(
        f"/drives/{source['drive']['id']}/items/{source['item']['id']}",
        headers=_write_headers(etag),
        retry_ambiguous=False,
        deadline=deadline,
        cancel_check=cancel_check,
    )
    return {
        "recycled": True,
        "item_id": source["item"]["id"],
        "drive_id": source["drive"]["id"],
    }


def _upload_source(config: SharePointConfiguration, source: Path | str) -> Path:
    root = config.upload_root
    _reject_symlink_components(root)
    candidate = Path(source).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    _reject_symlink_components(candidate)
    resolved = candidate.resolve(strict=False)
    if not _is_within(resolved, root.resolve(strict=False)):
        raise SharePointFileBoundaryError(
            "upload source is outside configured upload root"
        )
    try:
        info = candidate.lstat()
    except OSError:
        raise SharePointFileBoundaryError("upload source is unavailable") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SharePointFileBoundaryError(
            "upload source must be a regular non-symlink file"
        )
    return candidate


@contextmanager
def _staged_upload_source(
    config: SharePointConfiguration,
    source: Path | str,
    *,
    deadline=None,
    cancel_check=None,
):
    """Copy one no-follow validated source into a private immutable staging path."""
    candidate = _upload_source(config, source)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError:
        raise SharePointFileBoundaryError("upload source is unavailable") from None
    try:
        opened = os.fstat(descriptor)
        try:
            current = candidate.lstat()
            current_resolved = candidate.resolve(strict=True)
            root_resolved = config.upload_root.resolve(strict=True)
        except OSError:
            raise SharePointFileBoundaryError("upload source changed during validation") from None
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
            or not _is_within(current_resolved, root_resolved)
        ):
            raise SharePointFileBoundaryError("upload source changed during validation")
        if opened.st_size > config.max_bytes:
            raise SharePointFileBoundaryError(
                "upload source exceeds configured byte limit"
            )

        with tempfile.TemporaryDirectory(prefix=".sharepoint-upload-") as temp:
            staged = Path(temp) / "payload"
            copied = 0
            with os.fdopen(os.dup(descriptor), "rb") as source_handle, staged.open(
                "xb"
            ) as staged_handle:
                for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                    _control(deadline=deadline, cancel_check=cancel_check)
                    copied += len(chunk)
                    if copied > config.max_bytes:
                        raise SharePointFileBoundaryError(
                            "upload source exceeds configured byte limit"
                        )
                    staged_handle.write(chunk)
            if copied != opened.st_size:
                raise SharePointFileBoundaryError(
                    "upload source changed while it was being staged"
                )
            staged.chmod(0o600)
            yield staged, copied, candidate.name
    finally:
        os.close(descriptor)


async def upload_with_client(
    client,
    config,
    *,
    folder_url,
    source,
    name=None,
    conflict_behavior="fail",
    chunk_size=10 * 1024 * 1024,
    max_chunks=10000,
    deadline=None,
    cancel_check=None,
):
    _control(deadline=deadline, cancel_check=cancel_check)
    if conflict_behavior not in {"fail", "replace", "rename"}:
        raise SharePointWriteError("upload conflict behavior is invalid")
    with _staged_upload_source(
        config, source, deadline=deadline, cancel_check=cancel_check
    ) as (local, size, source_name):
        destination = await client.get_item(
            url=folder_url, deadline=deadline, cancel_check=cancel_check
        )
        if destination["item"].get("kind") != "folder":
            raise SharePointWriteError("upload destination must be a folder")
        remote_name = _write_name(name or source_name)
        encoded = quote(remote_name, safe="")
        base = f"/drives/{destination['drive']['id']}/items/{destination['item']['id']}:/{encoded}:"
        if size <= 4 * 1024 * 1024:
            raw = await client.graph.upload_small(
                f"{base}/content?@microsoft.graph.conflictBehavior={conflict_behavior}",
                local.read_bytes(),
                max_bytes=4 * 1024 * 1024,
                deadline=deadline,
                cancel_check=cancel_check,
            )
        else:
            raw = await client.graph.upload_via_session(
                f"{base}/createUploadSession",
                local,
                max_bytes=config.max_bytes,
                chunk_size=chunk_size,
                max_chunks=max_chunks,
                conflict_behavior=conflict_behavior,
                deadline=deadline,
                cancel_check=cancel_check,
            )
    return {
        "id": _bounded_text(raw.get("id"), "item id", 1024),
        "name": _bounded_text(raw.get("name"), "item name", 1024),
        "size": raw.get("size", size),
    }


async def _write_operation(name, configuration, arguments, *, cancel_check=None):
    config = SharePointConfiguration.from_runtime(configuration)
    client = client_from_configuration(configuration)
    deadline = time.monotonic() + config.timeout_seconds
    if name == "sharepoint_upload":
        return await upload_with_client(
            client,
            config,
            folder_url=arguments.get("folder_url"),
            source=arguments.get("source"),
            name=arguments.get("name"),
            conflict_behavior=arguments.get("conflict_behavior", "fail"),
            chunk_size=int(arguments.get("chunk_size", 10 * 1024 * 1024)),
            max_chunks=int(arguments.get("max_chunks", 10000)),
            deadline=deadline,
            cancel_check=cancel_check,
        )
    if name == "sharepoint_create_folder":
        return await create_folder_with_client(
            client,
            parent_url=arguments.get("parent_url"),
            name=arguments.get("name"),
            exist_ok=arguments.get("exist_ok") is True,
            etag=arguments.get("etag"),
            deadline=deadline,
            cancel_check=cancel_check,
        )
    if name == "sharepoint_move_item":
        return await move_item_with_client(
            client,
            source_url=arguments.get("source_url"),
            destination_url=arguments.get("destination_url"),
            name=arguments.get("name"),
            etag=arguments.get("etag"),
            deadline=deadline,
            cancel_check=cancel_check,
        )
    if name == "sharepoint_copy_item":
        return await copy_item_with_client(
            client,
            source_url=arguments.get("source_url"),
            destination_url=arguments.get("destination_url"),
            name=arguments.get("name"),
            max_polls=int(arguments.get("max_polls", 30)),
            deadline=deadline,
            cancel_check=cancel_check,
        )
    if name == "sharepoint_recycle_item":
        return await recycle_item_with_client(
            client,
            url=arguments.get("url"),
            etag=arguments.get("etag"),
            deadline=deadline,
            cancel_check=cancel_check,
        )
    raise SharePointWriteError("unknown write operation")


async def write_operation(name, configuration, arguments, *, cancel_check=None):
    try:
        return await _write_operation(
            name, configuration, arguments, cancel_check=cancel_check
        )
    except Exception as error:
        try:
            from tools.microsoft_graph_client import MicrosoftGraphAmbiguousWriteError
        except ImportError:
            raise error
        if isinstance(error, MicrosoftGraphAmbiguousWriteError):
            raise SharePointAmbiguousWriteError(
                "SharePoint write outcome requires reconciliation"
            ) from None
        raise
