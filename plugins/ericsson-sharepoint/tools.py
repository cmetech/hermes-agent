"""Model-visible schemas and dispatch for SharePoint identity operations."""

from __future__ import annotations

from typing import Any, Mapping

from . import operations


_COMMON_URL = {"type": "string", "minLength": 1, "maxLength": 8192}
SCHEMAS = {
    "sharepoint_resolve_url": {
        "name": "sharepoint_resolve_url",
        "description": "Resolve an authorized SharePoint URL to bounded site, drive, and item identity.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "minLength": 1, "maxLength": 8192}
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    "sharepoint_get_item": {
        "name": "sharepoint_get_item",
        "description": "Read bounded SharePoint DriveItem metadata by URL or explicit drive/item IDs.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "minLength": 1, "maxLength": 8192},
                "drive_id": {"type": "string", "minLength": 1, "maxLength": 1024},
                "item_id": {"type": "string", "minLength": 1, "maxLength": 1024},
            },
            "additionalProperties": False,
        },
    },
    "sharepoint_list_items": {
        "name": "sharepoint_list_items",
        "description": "List bounded SharePoint folder metadata with optional bounded recursion.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "minLength": 1, "maxLength": 8192},
                "drive_id": {"type": "string", "minLength": 1, "maxLength": 1024},
                "item_id": {"type": "string", "minLength": 1, "maxLength": 1024},
                "recursive": {"type": "boolean", "default": False},
                "max_depth": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 64,
                    "default": 3,
                },
                "max_pages": {"type": "integer", "minimum": 1, "maximum": 1000},
                "max_items": {"type": "integer", "minimum": 1, "maximum": 100000},
                "max_metadata_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 67108864,
                },
                "name_patterns": {
                    "type": "array",
                    "maxItems": 64,
                    "items": {"type": "string", "minLength": 1, "maxLength": 256},
                },
                "extensions": {
                    "type": "array",
                    "maxItems": 64,
                    "items": {"type": "string", "minLength": 1, "maxLength": 32},
                },
            },
            "additionalProperties": False,
        },
    },
    "sharepoint_download": {
        "name": "sharepoint_download",
        "description": "Stream one SharePoint file into the authorized local boundary.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "minLength": 1, "maxLength": 8192},
                "drive_id": {"type": "string", "minLength": 1, "maxLength": 1024},
                "item_id": {"type": "string", "minLength": 1, "maxLength": 1024},
                "destination": {"type": "string", "minLength": 1, "maxLength": 4096},
                "max_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1099511627776,
                },
            },
            "additionalProperties": False,
        },
    },
    "sharepoint_list_owned_sites": {
        "name": "sharepoint_list_owned_sites",
        "description": "List bounded SharePoint sites owned through Microsoft 365 groups.",
        "parameters": {
            "type": "object",
            "properties": {
                "max_pages": {"type": "integer", "minimum": 1, "maximum": 1000},
                "max_sites": {"type": "integer", "minimum": 1, "maximum": 100000},
                "max_metadata_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 67108864,
                },
            },
            "additionalProperties": False,
        },
    },
    "sharepoint_audit_permissions": {
        "name": "sharepoint_audit_permissions",
        "description": "Audit bounded permissions and site metadata through the enrolled same-origin browser.",
        "parameters": {
            "type": "object",
            "properties": {
                "sites": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "maxLength": 512},
                            "url": {"type": "string", "maxLength": 8192},
                        },
                        "required": ["name", "url"],
                        "additionalProperties": False,
                    },
                },
                "selected": {
                    "type": "array",
                    "maxItems": 100,
                    "items": {"type": "string", "maxLength": 8192},
                },
                "max_sites": {"type": "integer", "minimum": 1, "maximum": 100},
                "max_pages_per_category": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                },
                "max_rows_per_category": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100000,
                },
                "max_total_rows": {"type": "integer", "minimum": 1, "maximum": 100000},
                "max_total_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1099511627776,
                },
            },
            "required": ["sites"],
            "additionalProperties": False,
        },
    },
        "sharepoint_upload": {
            "name": "sharepoint_upload",
            "description": "Upload an authorized local file to a SharePoint folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_url": _COMMON_URL,
                    "source": {"type": "string", "minLength": 1, "maxLength": 4096},
                    "name": {"type": "string", "minLength": 1, "maxLength": 255},
                    "conflict_behavior": {
                        "type": "string",
                        "enum": ["fail", "replace", "rename"],
                    },
                    "chunk_size": {"type": "integer", "minimum": 327680},
                    "max_chunks": {"type": "integer", "minimum": 1, "maximum": 100000},
                },
                "required": ["folder_url", "source"],
                "additionalProperties": False,
            },
        },
        "sharepoint_create_folder": {
            "name": "sharepoint_create_folder",
            "description": "Create a SharePoint folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "parent_url": _COMMON_URL,
                    "name": {"type": "string", "minLength": 1, "maxLength": 255},
                    "exist_ok": {"type": "boolean"},
                    "etag": {"type": "string", "maxLength": 1024},
                },
                "required": ["parent_url", "name"],
                "additionalProperties": False,
            },
        },
        "sharepoint_move_item": {
            "name": "sharepoint_move_item",
            "description": "Move or rename a SharePoint item.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_url": _COMMON_URL,
                    "destination_url": _COMMON_URL,
                    "name": {"type": "string", "minLength": 1, "maxLength": 255},
                    "etag": {"type": "string", "maxLength": 1024},
                },
                "required": ["source_url"],
                "additionalProperties": False,
            },
        },
        "sharepoint_copy_item": {
            "name": "sharepoint_copy_item",
            "description": "Copy a SharePoint item and wait for bounded async completion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_url": _COMMON_URL,
                    "destination_url": _COMMON_URL,
                    "name": {"type": "string", "minLength": 1, "maxLength": 255},
                    "max_polls": {"type": "integer", "minimum": 1, "maximum": 1000},
                },
                "required": ["source_url", "destination_url"],
                "additionalProperties": False,
            },
        },
        "sharepoint_recycle_item": {
            "name": "sharepoint_recycle_item",
            "description": "Recycle a SharePoint item so site recovery remains available.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": _COMMON_URL,
                    "etag": {"type": "string", "maxLength": 1024},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
}


async def invoke(
    name: str,
    arguments: Mapping[str, Any],
    configuration: Mapping[str, Any],
    *,
    file_authorization=None,
    unattended: bool = False,
    cancel_check=None,
) -> dict[str, Any]:
    if name == "sharepoint_resolve_url":
        return await operations.resolve_url(
            configuration,
            str(arguments.get("url") or ""),
            cancel_check=cancel_check,
        )
    if name == "sharepoint_get_item":
        return await operations.get_item(
            configuration,
            url=arguments.get("url"),
            drive_id=arguments.get("drive_id"),
            item_id=arguments.get("item_id"),
            cancel_check=cancel_check,
        )
    if name == "sharepoint_list_items":
        return await operations.list_items(
            configuration,
            url=arguments.get("url"),
            drive_id=arguments.get("drive_id"),
            item_id=arguments.get("item_id"),
            recursive=arguments.get("recursive") is True,
            max_depth=int(arguments.get("max_depth", 3)),
            max_pages=arguments.get("max_pages"),
            max_items=arguments.get("max_items"),
            max_metadata_bytes=arguments.get("max_metadata_bytes"),
            name_patterns=tuple(arguments.get("name_patterns") or ()),
            extensions=tuple(arguments.get("extensions") or ()),
            cancel_check=cancel_check,
        )
    if name == "sharepoint_download":
        return await operations.download(
            configuration,
            url=arguments.get("url"),
            drive_id=arguments.get("drive_id"),
            item_id=arguments.get("item_id"),
            destination=arguments.get("destination"),
            max_bytes=arguments.get("max_bytes"),
            file_authorization=file_authorization,
            unattended=unattended,
            cancel_check=cancel_check,
        )
    if name == "sharepoint_list_owned_sites":
        return await operations.list_owned_sites(
            configuration,
            max_pages=arguments.get("max_pages"),
            max_sites=arguments.get("max_sites"),
            max_metadata_bytes=arguments.get("max_metadata_bytes"),
            cancel_check=cancel_check,
        )
    if name == "sharepoint_audit_permissions":
        limits = {
            key: arguments[key]
            for key in (
                "max_sites",
                "max_pages_per_category",
                "max_rows_per_category",
                "max_total_rows",
                "max_total_bytes",
            )
            if key in arguments
        }
        return await operations.audit_permissions(
            configuration,
            sites=arguments.get("sites") or [],
            selected=tuple(arguments.get("selected") or ()),
            cancel_check=cancel_check,
            **limits,
        )
    if name in {
        "sharepoint_upload",
        "sharepoint_create_folder",
        "sharepoint_move_item",
        "sharepoint_copy_item",
        "sharepoint_recycle_item",
    }:
        return await operations.write_operation(
            name, configuration, arguments, cancel_check=cancel_check
        )
    raise ValueError("unknown SharePoint tool")
