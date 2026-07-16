"""Verified model capability fetching, validation, joining, and caching."""

from __future__ import annotations

import hashlib
import json
import math
import time
import urllib.request
from dataclasses import dataclass
from typing import Literal, Sequence, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit

from hermes_cli.urllib_security import open_credentialed_url
from providers import get_provider_profile


CapabilityState = Literal["supported", "unsupported", "unknown"]
SelectionMode = Literal["automatic", "explicit"]
CapabilityCatalogStatus = Literal[
    "ready",
    "authentication-required",
    "gateway-upgrade-required",
    "gateway-unreachable",
    "catalog-empty",
    "capability-response-invalid",
    "unknown",
]

CAPABILITY_KEYS = ("completion", "tools", "vision", "reasoning")
_CAPABILITY_STATES = frozenset(("supported", "unsupported", "unknown"))
_SELECTION_MODES = frozenset(("automatic", "explicit"))
MODEL_CAPABILITIES_CACHE_TTL = 3600
_CACHE_VERSION = 1


@dataclass(frozen=True)
class VerifiedModelCapability:
    id: str
    name: str
    selection_mode: SelectionMode
    capabilities: dict[str, CapabilityState]
    evidence: dict[str, dict[str, str]]


@dataclass(frozen=True)
class ModelCapabilityCatalog:
    status: CapabilityCatalogStatus
    models: dict[str, VerifiedModelCapability]
    registry_revision: str = ""
    generated_at: str = ""
    detail: str = ""


def _invalid_catalog(detail: str = "invalid capability response") -> ModelCapabilityCatalog:
    return ModelCapabilityCatalog(
        status="capability-response-invalid",
        models={},
        detail=detail,
    )


def _failure_catalog(
    status: CapabilityCatalogStatus,
    detail: str,
) -> ModelCapabilityCatalog:
    return ModelCapabilityCatalog(status=status, models={}, detail=detail)


def _parse_evidence(raw: object) -> dict[str, dict[str, str]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("evidence must be an object")

    evidence: dict[str, dict[str, str]] = {}
    for capability, raw_fields in raw.items():
        if not isinstance(capability, str) or not isinstance(raw_fields, dict):
            raise ValueError("invalid evidence")
        fields: dict[str, str] = {}
        for key, value in raw_fields.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("invalid evidence field")
            fields[key] = value
        evidence[capability] = fields
    return evidence


def _parse_catalog_payload(payload: object) -> ModelCapabilityCatalog:
    if not isinstance(payload, dict):
        raise ValueError("response must be an object")
    if payload.get("object") != "list":
        raise ValueError("object must be list")

    registry_revision = payload.get("registry_revision")
    generated_at = payload.get("generated_at")
    raw_models = payload.get("data")
    if not isinstance(registry_revision, str):
        raise ValueError("registry_revision must be a string")
    if not isinstance(generated_at, str):
        raise ValueError("generated_at must be a string")
    if not isinstance(raw_models, list):
        raise ValueError("data must be a list")

    models: dict[str, VerifiedModelCapability] = {}
    explicit_count = 0
    for raw_model in raw_models:
        if not isinstance(raw_model, dict):
            raise ValueError("model entry must be an object")

        model_id = raw_model.get("id")
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model id must be non-empty")
        if model_id in models:
            raise ValueError("duplicate model id")

        name = raw_model.get("name", model_id)
        if not isinstance(name, str):
            raise ValueError("model name must be a string")

        selection_mode = raw_model.get("selection_mode")
        if selection_mode not in _SELECTION_MODES:
            raise ValueError("invalid selection mode")
        if not isinstance(raw_model.get("available"), bool):
            raise ValueError("available must be a boolean")
        if selection_mode == "explicit" and raw_model["available"] is not True:
            raise ValueError("explicit model must be available")

        raw_capabilities = raw_model.get("capabilities")
        if not isinstance(raw_capabilities, dict):
            raise ValueError("capabilities must be an object")

        capabilities: dict[str, CapabilityState] = {}
        for key in CAPABILITY_KEYS:
            state = raw_capabilities.get(key, "unknown")
            if state not in _CAPABILITY_STATES:
                raise ValueError("invalid capability state")
            capabilities[key] = cast(CapabilityState, state)

        models[model_id] = VerifiedModelCapability(
            id=model_id,
            name=name,
            selection_mode=cast(SelectionMode, selection_mode),
            capabilities=capabilities,
            evidence=_parse_evidence(raw_model.get("evidence", {})),
        )
        if selection_mode == "explicit":
            explicit_count += 1

    return ModelCapabilityCatalog(
        status="ready" if explicit_count else "catalog-empty",
        models=models,
        registry_revision=registry_revision,
        generated_at=generated_at,
    )


def _normalize_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url.strip())
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("base URL must use HTTP(S) with a hostname")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"

    port = parsed.port
    if port is not None and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    ):
        hostname = f"{hostname}:{port}"

    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, hostname, path, parsed.query, ""))


def _resolve_capabilities_url(base_url: str, relative_path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", relative_path.lstrip("/"))


def _credential_fingerprint(
    api_key: str,
    default_headers: dict[str, str],
) -> str:
    effective_headers = {
        name.lower(): value for name, value in default_headers.items()
    }
    if api_key:
        effective_headers["authorization"] = f"Bearer {api_key}"
    material = json.dumps(
        sorted(effective_headers.items()),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.blake2b(
        material.encode("utf-8", errors="replace"),
        digest_size=8,
    ).hexdigest()


def _base_url_fingerprint(normalized_base_url: str) -> str:
    return hashlib.blake2b(
        normalized_base_url.encode("utf-8", errors="replace"),
        digest_size=8,
    ).hexdigest()


def _cache_entry_key(
    provider: str,
    normalized_base_url: str,
    credential_fingerprint: str,
) -> str:
    material = "\0".join(
        (provider, normalized_base_url, credential_fingerprint)
    ).encode("utf-8", errors="replace")
    return hashlib.blake2b(material, digest_size=16).hexdigest()


def _cache_path():
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "model_capabilities_cache.json"


def _load_cache() -> dict:
    try:
        path = _cache_path()
        if not path.exists():
            return {"version": _CACHE_VERSION, "entries": {}}
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if (
            not isinstance(data, dict)
            or data.get("version") != _CACHE_VERSION
            or not isinstance(data.get("entries"), dict)
        ):
            return {"version": _CACHE_VERSION, "entries": {}}
        return data
    except Exception:
        return {"version": _CACHE_VERSION, "entries": {}}


def _save_cache(cache: dict) -> None:
    try:
        from utils import atomic_json_write

        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(path, cache, indent=None)
    except Exception:
        pass


def _catalog_payload(catalog: ModelCapabilityCatalog) -> dict:
    return {
        "object": "list",
        "registry_revision": catalog.registry_revision,
        "generated_at": catalog.generated_at,
        "data": [
            {
                "id": model.id,
                "name": model.name,
                "available": True,
                "selection_mode": model.selection_mode,
                "capabilities": dict(model.capabilities),
                "evidence": {
                    capability: dict(fields)
                    for capability, fields in model.evidence.items()
                },
            }
            for model in catalog.models.values()
        ],
    }


def _cached_catalog(entry: object, *, now: float) -> ModelCapabilityCatalog | None:
    if not isinstance(entry, dict):
        return None
    try:
        cached_at = float(entry["at"])
    except (KeyError, TypeError, ValueError):
        return None
    age = now - cached_at
    if not math.isfinite(cached_at) or not (
        0 <= age < MODEL_CAPABILITIES_CACHE_TTL
    ):
        return None
    try:
        return _parse_catalog_payload(entry["payload"])
    except (KeyError, TypeError, ValueError):
        return None


def fetch_model_capability_catalog(
    provider: str,
    *,
    api_key: str,
    base_url: str,
    force_refresh: bool = False,
    timeout: float = 8.0,
) -> ModelCapabilityCatalog:
    """Fetch and cache a provider's structurally verified capability catalog."""
    profile = get_provider_profile(provider)
    if profile is None or not profile.model_capabilities_path:
        return _failure_catalog(
            "unknown",
            "provider does not declare a capability endpoint",
        )

    raw_base_url = base_url or profile.base_url
    if not isinstance(raw_base_url, str):
        return _failure_catalog("unknown", "provider base URL is invalid")
    effective_base_url = raw_base_url.strip()
    if not effective_base_url:
        return _failure_catalog("unknown", "provider base URL is unavailable")

    canonical_provider = profile.name or provider
    try:
        normalized_base_url = _normalize_base_url(effective_base_url)
        request = urllib.request.Request(
            _resolve_capabilities_url(
                effective_base_url,
                profile.model_capabilities_path,
            ),
            method="GET",
        )
        request.add_header("Accept", "application/json")
        for name, value in profile.default_headers.items():
            request.add_header(name, value)
        if api_key:
            request.add_header("Authorization", f"Bearer {api_key}")
    except (TypeError, ValueError):
        return _failure_catalog("unknown", "provider base URL is invalid")

    credential_fp = _credential_fingerprint(
        api_key or "",
        profile.default_headers,
    )
    cache_key = _cache_entry_key(
        canonical_provider,
        normalized_base_url,
        credential_fp,
    )
    cache = _load_cache()
    now = time.time()
    if not force_refresh:
        cached = _cached_catalog(cache["entries"].get(cache_key), now=now)
        if cached is not None:
            return cached

    try:
        with open_credentialed_url(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            body = response.read()
    except HTTPError as exc:
        if exc.code in (401, 403):
            return _failure_catalog(
                "authentication-required",
                "gateway authentication is required",
            )
        if exc.code == 404:
            return _failure_catalog(
                "gateway-upgrade-required",
                "gateway capability endpoint is unavailable",
            )
        if 500 <= exc.code < 600:
            return _failure_catalog(
                "gateway-unreachable",
                "gateway request failed",
            )
        return _failure_catalog("unknown", "gateway request was rejected")
    except (TimeoutError, URLError, ConnectionError, OSError):
        return _failure_catalog(
            "gateway-unreachable",
            "gateway could not be reached",
        )
    except Exception:
        return _failure_catalog("unknown", "gateway request failed")

    if status in (401, 403):
        return _failure_catalog(
            "authentication-required",
            "gateway authentication is required",
        )
    if status == 404:
        return _failure_catalog(
            "gateway-upgrade-required",
            "gateway capability endpoint is unavailable",
        )
    if 500 <= status < 600:
        return _failure_catalog("gateway-unreachable", "gateway request failed")
    if status != 200:
        return _failure_catalog("unknown", "gateway request was rejected")

    try:
        payload = json.loads(body.decode("utf-8"))
        catalog = _parse_catalog_payload(payload)
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return _invalid_catalog()

    cache["entries"][cache_key] = {
        "provider": canonical_provider,
        "base_url_fingerprint": _base_url_fingerprint(normalized_base_url),
        "credential_fingerprint": credential_fp,
        "at": now,
        "payload": _catalog_payload(catalog),
    }
    _save_cache(cache)
    return catalog


def clear_model_capabilities_cache(provider: str | None = None) -> None:
    """Clear every capability entry or only entries for one provider."""
    try:
        path = _cache_path()
        if provider is None:
            if path.exists():
                path.unlink()
            return

        profile = get_provider_profile(provider)
        canonical_provider = profile.name if profile is not None else provider
        cache = _load_cache()
        entries = cache["entries"]
        filtered = {
            key: entry
            for key, entry in entries.items()
            if not (
                isinstance(entry, dict)
                and entry.get("provider") == canonical_provider
            )
        }
        if len(filtered) != len(entries):
            cache["entries"] = filtered
            _save_cache(cache)
    except Exception:
        pass


def _unknown_capability(
    model_id: str,
    *,
    selection_mode: SelectionMode,
    name: str | None = None,
) -> VerifiedModelCapability:
    return VerifiedModelCapability(
        id=model_id,
        name=name or model_id,
        selection_mode=selection_mode,
        capabilities={
            key: cast(CapabilityState, "unknown") for key in CAPABILITY_KEYS
        },
        evidence={},
    )


def join_live_model_capabilities(
    live_model_ids: Sequence[str],
    catalog: ModelCapabilityCatalog,
) -> tuple[list[str], dict[str, VerifiedModelCapability], int]:
    """Join live availability to capability metadata using exact model IDs."""
    normalized_live = ["auto"]
    seen = {"auto"}
    for model_id in live_model_ids:
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        if model_id in seen:
            continue
        seen.add(model_id)
        normalized_live.append(model_id)

    metadata: dict[str, VerifiedModelCapability] = {}
    auto = catalog.models.get("auto")
    metadata["auto"] = _unknown_capability(
        "auto",
        selection_mode="automatic",
        name=auto.name if auto is not None else "Automatic",
    )
    for model_id in normalized_live[1:]:
        metadata[model_id] = catalog.models.get(model_id) or _unknown_capability(
            model_id,
            selection_mode="explicit",
        )

    live_explicit_ids = set(normalized_live[1:])
    catalog_explicit_ids = {
        model_id for model_id in catalog.models if model_id != "auto"
    }
    mismatch_count = len(live_explicit_ids.symmetric_difference(catalog_explicit_ids))
    return normalized_live, metadata, mismatch_count


__all__ = [
    "CAPABILITY_KEYS",
    "MODEL_CAPABILITIES_CACHE_TTL",
    "CapabilityCatalogStatus",
    "CapabilityState",
    "ModelCapabilityCatalog",
    "SelectionMode",
    "VerifiedModelCapability",
    "clear_model_capabilities_cache",
    "fetch_model_capability_catalog",
    "join_live_model_capabilities",
]
