"""SharePoint identity resolution over the generic Microsoft Graph client."""

from __future__ import annotations

import base64
from typing import Any, Collection, Mapping
from urllib.parse import quote, unquote, urlsplit

from .models import SharePointResolutionError
from .url_parser import ParsedSharePointURL, parse_sharepoint_url


_DEFAULT_LIBRARIES = frozenset({"", "documents", "shared documents"})
_MAX_ID_LENGTH = 1024
_ITEM_SELECT = (
    "id,name,size,file,folder,webUrl,parentReference,createdDateTime,"
    "lastModifiedDateTime,createdBy,lastModifiedBy"
)


def encode_share_id(url: str) -> str:
    """Return Graph's ``u!`` sharing-token encoding for an HTTPS URL."""
    encoded = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii")
    return "u!" + encoded.rstrip("=")


def _safe_id(value: Any, name: str) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > _MAX_ID_LENGTH
        or any(character in normalized for character in "/\\?#\x00")
        or any(ord(character) < 32 for character in normalized)
    ):
        raise SharePointResolutionError(f"{name} is invalid")
    return normalized


class SharePointClient:
    """Resolve tenant URLs to bounded site, drive, and DriveItem identities."""

    def __init__(
        self,
        graph_client,
        *,
        tenant_hosts: Collection[str],
        max_pages: int = 25,
        max_items: int = 1000,
    ) -> None:
        hosts = tuple(
            dict.fromkeys(str(host).strip().lower().rstrip(".") for host in tenant_hosts)
        )
        if not hosts:
            raise SharePointResolutionError("at least one tenant host is required")
        self.graph = graph_client
        self.tenant_hosts = frozenset(hosts)
        self.max_pages = max(1, min(int(max_pages), 1000))
        self.max_items = max(1, min(int(max_items), 100_000))

    def _safe_web_url(self, value: Any) -> str:
        if value in {None, ""}:
            return ""
        if not isinstance(value, str) or len(value) > 8192:
            raise SharePointResolutionError("Graph returned an invalid web URL")
        try:
            parts = urlsplit(value)
            port = parts.port
        except ValueError:
            raise SharePointResolutionError("Graph returned an invalid web URL") from None
        host = (parts.hostname or "").lower().rstrip(".")
        if (
            parts.scheme.lower() != "https"
            or host not in self.tenant_hosts
            or parts.username is not None
            or parts.password is not None
            or port not in {None, 443}
        ):
            raise SharePointResolutionError(
                "Graph result escaped the configured tenant authority"
            )
        return value

    @staticmethod
    def _mapping(value: Any, label: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise SharePointResolutionError(f"Graph returned invalid {label} metadata")
        return value

    async def resolve_url(self, url: str) -> dict[str, Any]:
        parsed = parse_sharepoint_url(url, allowed_hosts=self.tenant_hosts)
        if parsed.kind == "sharing":
            return await self._resolve_sharing(parsed)
        return await self._resolve_path(parsed)

    async def _resolve_path(self, parsed: ParsedSharePointURL) -> dict[str, Any]:
        site_ref = (
            f"{parsed.host}:/{quote(parsed.site_path, safe='/')}"
            if parsed.site_path
            else parsed.host
        )
        site = self._mapping(
            await self.graph.get_json(
                f"/sites/{site_ref}",
                params={"$select": "id,displayName,webUrl"},
            ),
            "site",
        )
        site_id = _safe_id(site.get("id"), "site id")
        site_url = self._safe_web_url(site.get("webUrl"))
        drive_id, drive_name = await self._resolve_drive(site_id, parsed.library)
        item_path = parsed.item_path
        item_endpoint = self._item_endpoint(drive_id, item_path)
        item = self._mapping(
            await self.graph.get_json(
                item_endpoint, params={"$select": _ITEM_SELECT}
            ),
            "DriveItem",
        )
        return self._projection(
            tenant_host=parsed.host,
            site_id=site_id,
            site_name=site.get("displayName"),
            site_url=site_url,
            drive_id=drive_id,
            drive_name=drive_name,
            item=item,
            item_path=item_path,
        )

    async def _resolve_drive(self, site_id: str, library: str) -> tuple[str, str]:
        if library.casefold() in _DEFAULT_LIBRARIES or library.startswith("_"):
            drive = self._mapping(
                await self.graph.get_json(
                    f"/sites/{site_id}/drive", params={"$select": "id,name"}
                ),
                "drive",
            )
            return _safe_id(drive.get("id"), "drive id"), self._safe_text(
                drive.get("name") or "Documents", "drive name"
            )

        matches: dict[str, str] = {}
        pages = 0
        items = 0
        async for page in self.graph.iterate_pages(
            f"/sites/{site_id}/drives",
            params={"$select": "id,name,webUrl", "$top": min(200, self.max_items)},
        ):
            pages += 1
            if pages > self.max_pages:
                raise SharePointResolutionError("drive enumeration exceeded page limit")
            page = self._mapping(page, "drive page")
            values = page.get("value")
            if not isinstance(values, list):
                raise SharePointResolutionError("Graph returned invalid drive enumeration")
            for raw in values:
                items += 1
                if items > self.max_items:
                    raise SharePointResolutionError("drive enumeration exceeded item limit")
                drive = self._mapping(raw, "drive")
                drive_id = _safe_id(drive.get("id"), "drive id")
                name = self._safe_text(drive.get("name") or "", "drive name")
                web_url = self._safe_web_url(drive.get("webUrl"))
                web_name = (
                    unquote(urlsplit(web_url).path.rstrip("/").rsplit("/", 1)[-1])
                    if web_url
                    else ""
                )
                if library.casefold() in {name.casefold(), web_name.casefold()}:
                    matches[drive_id] = name or library
        if not matches:
            raise SharePointResolutionError("document library was not found")
        if len(matches) != 1:
            raise SharePointResolutionError("document library identity is ambiguous")
        return next(iter(matches.items()))

    async def _resolve_sharing(self, parsed: ParsedSharePointURL) -> dict[str, Any]:
        item = self._mapping(
            await self.graph.get_json(
                f"/shares/{encode_share_id(parsed.sharing_url)}/driveItem",
                params={"$select": _ITEM_SELECT},
            ),
            "shared DriveItem",
        )
        parent = self._mapping(item.get("parentReference") or {}, "parent reference")
        site_id = _safe_id(parent.get("siteId"), "site id")
        drive_id = _safe_id(parent.get("driveId"), "drive id")
        return self._projection(
            tenant_host=parsed.host,
            site_id=site_id,
            site_name="",
            site_url="",
            drive_id=drive_id,
            drive_name="",
            item=item,
            item_path="",
        )

    async def get_item(
        self,
        *,
        url: str | None = None,
        drive_id: str | None = None,
        item_id: str | None = None,
    ) -> dict[str, Any]:
        if url:
            if drive_id or item_id:
                raise SharePointResolutionError("URL and explicit IDs are ambiguous")
            return await self.resolve_url(url)
        if not drive_id or not item_id:
            raise SharePointResolutionError("drive_id and item_id are required together")
        safe_drive = _safe_id(drive_id, "drive id")
        safe_item = _safe_id(item_id, "item id")
        item = self._mapping(
            await self.graph.get_json(
                f"/drives/{safe_drive}/items/{safe_item}",
                params={"$select": _ITEM_SELECT},
            ),
            "DriveItem",
        )
        parent = self._mapping(item.get("parentReference") or {}, "parent reference")
        site_id = str(parent.get("siteId") or "")
        if site_id:
            site_id = _safe_id(site_id, "site id")
        host = next(iter(self.tenant_hosts)) if len(self.tenant_hosts) == 1 else ""
        return self._projection(
            tenant_host=host,
            site_id=site_id,
            site_name="",
            site_url="",
            drive_id=safe_drive,
            drive_name="",
            item=item,
            item_path="",
        )

    @staticmethod
    def _item_endpoint(drive_id: str, item_path: str) -> str:
        if not item_path:
            return f"/drives/{drive_id}/root"
        encoded = "/".join(quote(segment, safe="") for segment in item_path.split("/"))
        return f"/drives/{drive_id}/root:/{encoded}:"

    @staticmethod
    def _safe_text(value: Any, label: str, maximum: int = 4096) -> str:
        text = str(value or "")
        if len(text) > maximum or any(ord(character) < 32 for character in text):
            raise SharePointResolutionError(f"Graph returned invalid {label}")
        return text

    def _projection(
        self,
        *,
        tenant_host: str,
        site_id: str,
        site_name: Any,
        site_url: str,
        drive_id: str,
        drive_name: Any,
        item: Mapping[str, Any],
        item_path: str,
    ) -> dict[str, Any]:
        item_id = _safe_id(item.get("id"), "item id")
        name = self._safe_text(item.get("name"), "item name")
        parent = self._mapping(item.get("parentReference") or {}, "parent reference")
        size = item.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            size = 0
        file_facet = item.get("file") if isinstance(item.get("file"), Mapping) else {}
        kind = "folder" if isinstance(item.get("folder"), Mapping) else "file" if file_facet else "unknown"
        return {
            "tenant_host": tenant_host,
            "site": {
                "id": site_id,
                "name": self._safe_text(site_name, "site name"),
                "web_url": site_url,
            },
            "drive": {
                "id": drive_id,
                "name": self._safe_text(drive_name, "drive name"),
            },
            "item": {
                "id": item_id,
                "name": name,
                "path": item_path,
                "kind": kind,
                "size": size,
                "web_url": self._safe_web_url(item.get("webUrl")),
                "parent_id": self._safe_text(parent.get("id"), "parent id", 1024),
                "mime_type": self._safe_text(file_facet.get("mimeType"), "MIME type", 512),
            },
        }


__all__ = ["SharePointClient", "encode_share_id"]

