"""Pure portable model-reference parsing and resolution for workflows."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping

import yaml


_TIERS = frozenset({"small", "medium", "large"})
_MAX_CONFIG_BYTES = 1_000_000
_MAX_ALIASES = 128
_MAX_NAME_CHARS = 80
_MAX_MODEL_CHARS = 512
_MAX_PROVIDER_CHARS = 128
_MAX_URL_CHARS = 2048
_MAX_OPTIONS = 16
_MAX_OPTION_DEPTH = 3
_MAX_OPTION_STRING_CHARS = 256
_ALLOWED_OPTIONS = frozenset(
    {
        "effort",
        "thinking",
        "web_execution",
        "service_tier",
        "betas",
    }
)
_SECRET_KEY_PARTS = (
    "api_key",
    "credential",
    "secret",
    "token",
    "password",
    "header",
    "environment",
)


class ModelResolutionError(ValueError):
    """Stable-code failure raised by pure workflow model resolution."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ModelResolutionIssue:
    code: str
    path: str
    message: str
    severity: Literal["error", "warning"] = "error"


@dataclass(frozen=True, slots=True)
class ResolutionDiagnostic:
    code: str
    rationale: str


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class ConfiguredModelEntry:
    name: str
    reference_kind: Literal["tier", "configured_alias"]
    provider: str
    model: str
    options: Mapping[str, Any]
    config_scope: Literal["profile", "managed"]
    base_url: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", _freeze_mapping(self.options))

    def public_identity(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "reference_kind": self.reference_kind,
            "provider": self.provider,
            "model": self.model,
            "options": _thaw(self.options),
            "config_scope": self.config_scope,
            "base_url_sha256": (
                hashlib.sha256(self.base_url.encode("utf-8")).hexdigest()
                if self.base_url
                else ""
            ),
        }


@dataclass(frozen=True, slots=True)
class WorkflowModelConfigSnapshot:
    tiers: Mapping[str, ConfiguredModelEntry]
    aliases: Mapping[str, ConfiguredModelEntry]
    active_provider: str
    active_provider_scope: Literal["profile", "managed"]
    active_base_url: str = field(repr=False)
    active_api_mode: str
    config_fingerprint: str
    issues: tuple[ModelResolutionIssue, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "tiers", MappingProxyType(dict(self.tiers)))
        object.__setattr__(self, "aliases", MappingProxyType(dict(self.aliases)))

    def to_dict(self) -> dict[str, Any]:
        """Return a credential-free projection; route URLs remain digest-only."""

        return {
            "tiers": {
                name: entry.public_identity()
                for name, entry in sorted(self.tiers.items())
            },
            "aliases": {
                name: entry.public_identity()
                for name, entry in sorted(self.aliases.items())
            },
            "active_provider": self.active_provider,
            "active_provider_scope": self.active_provider_scope,
            "active_base_url_sha256": (
                hashlib.sha256(self.active_base_url.encode("utf-8")).hexdigest()
                if self.active_base_url
                else ""
            ),
            "active_api_mode": self.active_api_mode,
            "config_fingerprint": self.config_fingerprint,
            "issues": [
                {
                    "code": issue.code,
                    "path": issue.path,
                    "message": issue.message,
                    "severity": issue.severity,
                }
                for issue in self.issues
            ],
        }


@dataclass(frozen=True, slots=True)
class ResolvedModelRoute:
    requested_reference: str
    reference_kind: Literal["tier", "configured_alias", "literal"]
    provider: str
    model: str
    api_mode: str
    route_fingerprint: str
    registration_provenance_digest: str
    provider_options: Mapping[str, Any]
    config_scope: Literal["profile", "managed"]
    base_url_trust_class: str
    warnings: tuple[ResolutionDiagnostic, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "provider_options", MappingProxyType(dict(self.provider_options))
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_reference": self.requested_reference,
            "reference_kind": self.reference_kind,
            "provider": self.provider,
            "model": self.model,
            "api_mode": self.api_mode,
            "route_fingerprint": self.route_fingerprint,
            "registration_provenance_digest": self.registration_provenance_digest,
            "provider_options": _thaw(self.provider_options),
            "config_scope": self.config_scope,
            "base_url_trust_class": self.base_url_trust_class,
            "warnings": [
                {"code": warning.code, "rationale": warning.rationale}
                for warning in self.warnings
            ],
        }


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _freeze_option_value(value: Any, *, depth: int = 0) -> Any:
    if depth > _MAX_OPTION_DEPTH:
        raise ValueError("option depth exceeded")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("option number must be finite")
        return value
    if isinstance(value, str):
        if len(value) > _MAX_OPTION_STRING_CHARS:
            raise ValueError("option string is too long")
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_OPTIONS:
            raise ValueError("option sequence is too large")
        return tuple(
            _freeze_option_value(item, depth=depth + 1) for item in value
        )
    if isinstance(value, Mapping):
        if len(value) > _MAX_OPTIONS:
            raise ValueError("option mapping is too large")
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > _MAX_NAME_CHARS:
                raise ValueError("option key is invalid")
            lowered = key.lower()
            if any(part in lowered for part in _SECRET_KEY_PARTS):
                raise ValueError("secret-like option key is forbidden")
            frozen[key] = _freeze_option_value(item, depth=depth + 1)
        return MappingProxyType(frozen)
    raise ValueError("option value is not JSON-safe")


def _parse_options(value: object) -> Mapping[str, Any] | None:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping) or len(value) > _MAX_OPTIONS:
        return None
    if any(
        not isinstance(key, str)
        or key not in _ALLOWED_OPTIONS
        or any(part in key.lower() for part in _SECRET_KEY_PARTS)
        for key in value
    ):
        return None
    try:
        frozen = _freeze_option_value(value)
        encoded = json.dumps(
            _thaw(frozen), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    if len(encoded) > 2048:
        return None
    return frozen


def _deep_merge(base: object, override: object) -> object:
    if isinstance(base, Mapping) and isinstance(override, Mapping):
        merged = {str(key): value for key, value in base.items()}
        for key, value in override.items():
            merged[str(key)] = _deep_merge(merged.get(str(key)), value)
        return merged
    return override


def _entry_scope(
    *,
    name: str,
    root: str,
    managed_config: Mapping[str, Any],
) -> Literal["profile", "managed"]:
    managed_root = managed_config.get(root)
    if isinstance(managed_root, Mapping) and name in managed_root:
        return "managed"
    return "profile"


def _issue(
    issues: list[ModelResolutionIssue],
    code: str,
    path: str,
    message: str,
) -> None:
    issues.append(ModelResolutionIssue(code=code, path=path, message=message))


def _parse_rich_entry(
    *,
    name: object,
    value: object,
    kind: Literal["tier", "configured_alias"],
    scope: Literal["profile", "managed"],
    path: str,
    issues: list[ModelResolutionIssue],
) -> ConfiguredModelEntry | None:
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name.strip()) > _MAX_NAME_CHARS
    ):
        _issue(issues, "model_reference_name_invalid", path, "name is invalid")
        return None
    if not isinstance(value, Mapping):
        _issue(issues, "model_reference_entry_invalid", path, "entry must be a mapping")
        return None
    provider = value.get("provider")
    model = value.get("model")
    if not isinstance(provider, str) or not provider.strip():
        _issue(
            issues,
            "model_reference_provider_missing",
            f"{path}.provider",
            "provider must be a nonempty string",
        )
        return None
    if not isinstance(model, str) or not model.strip():
        _issue(
            issues,
            "model_reference_model_missing",
            f"{path}.model",
            "model must be a nonempty string",
        )
        return None
    provider = provider.strip()
    model = model.strip()
    if len(provider) > _MAX_PROVIDER_CHARS or len(model) > _MAX_MODEL_CHARS:
        _issue(issues, "model_reference_entry_invalid", path, "entry text is too long")
        return None
    options = _parse_options(value.get("options"))
    if options is None:
        _issue(
            issues,
            "model_reference_options_invalid",
            f"{path}.options",
            "options are unbounded, secret-like, unknown, or not JSON-safe",
        )
        return None
    forbidden_entry_keys = {
        str(key)
        for key in value
        if any(part in str(key).lower() for part in _SECRET_KEY_PARTS)
    }
    if forbidden_entry_keys:
        _issue(
            issues,
            "model_reference_secret_key_forbidden",
            path,
            "secret-like keys are forbidden in portable model entries",
        )
        return None
    base_url = value.get("base_url", "")
    if not isinstance(base_url, str) or len(base_url) > _MAX_URL_CHARS:
        _issue(issues, "model_reference_entry_invalid", f"{path}.base_url", "base_url is invalid")
        return None
    return ConfiguredModelEntry(
        name=name.strip().lower(),
        reference_kind=kind,
        provider=provider,
        model=model,
        options=options,
        config_scope=scope,
        base_url=base_url.strip().rstrip("/"),
    )


def _legacy_alias_entry(
    *,
    name: object,
    value: object,
    current_provider: str,
    scope: Literal["profile", "managed"],
    issues: list[ModelResolutionIssue],
) -> ConfiguredModelEntry | None:
    path = f"model.aliases.{name}"
    if not isinstance(value, str) or not value.strip():
        _issue(issues, "model_reference_entry_invalid", path, "legacy alias must be a string")
        return None
    text = value.strip()
    if "/" in text:
        provider, model = text.split("/", 1)
    else:
        provider, model = current_provider, text
    return _parse_rich_entry(
        name=name,
        value={"provider": provider, "model": model},
        kind="configured_alias",
        scope=scope,
        path=path,
        issues=issues,
    )


def _fingerprint_payload(
    *,
    tiers: Mapping[str, ConfiguredModelEntry],
    aliases: Mapping[str, ConfiguredModelEntry],
    active_provider: str,
    active_provider_scope: str,
    active_base_url: str,
    active_api_mode: str,
    issues: tuple[ModelResolutionIssue, ...],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "tiers": {
            name: entry.public_identity() for name, entry in sorted(tiers.items())
        },
        "aliases": {
            name: entry.public_identity() for name, entry in sorted(aliases.items())
        },
        "active_provider": active_provider,
        "active_provider_scope": active_provider_scope,
        "active_base_url_sha256": (
            hashlib.sha256(active_base_url.encode("utf-8")).hexdigest()
            if active_base_url
            else ""
        ),
        "active_api_mode": active_api_mode,
        "issues": [
            {"code": issue.code, "path": issue.path, "severity": issue.severity}
            for issue in issues
        ],
    }


def parse_workflow_model_config(
    profile_config: Mapping[str, Any] | object,
    *,
    managed_config: Mapping[str, Any] | object | None = None,
) -> WorkflowModelConfigSnapshot:
    """Parse only profile and managed config authorities into a frozen snapshot."""

    profile = dict(profile_config) if isinstance(profile_config, Mapping) else {}
    managed = dict(managed_config) if isinstance(managed_config, Mapping) else {}
    merged = _deep_merge(profile, managed)
    if not isinstance(merged, Mapping):
        merged = {}
    issues: list[ModelResolutionIssue] = []
    for unsupported_root in ("project_model_aliases", "package_model_aliases"):
        if unsupported_root in profile or unsupported_root in managed:
            _issue(
                issues,
                "model_reference_source_unsupported",
                unsupported_root,
                "project and package alias authorities are not supported",
            )

    model_section = merged.get("model")
    model_section = model_section if isinstance(model_section, Mapping) else {}
    active_provider = str(model_section.get("provider") or "").strip()
    active_base_url = ""
    for key in ("base_url", "api", "url"):
        value = model_section.get(key)
        if isinstance(value, str) and value.strip():
            active_base_url = value.strip().rstrip("/")
            break
    active_api_mode = str(model_section.get("api_mode") or "").strip().lower()
    managed_model = managed.get("model")
    active_provider_scope: Literal["profile", "managed"] = (
        "managed"
        if isinstance(managed_model, Mapping)
        and any(
            key in managed_model
            for key in ("provider", "base_url", "api", "url", "api_mode")
        )
        else "profile"
    )

    tiers: dict[str, ConfiguredModelEntry] = {}
    tier_root = merged.get("model_tiers")
    if tier_root is not None and not isinstance(tier_root, Mapping):
        _issue(issues, "model_tiers_invalid", "model_tiers", "model_tiers must be a mapping")
    elif isinstance(tier_root, Mapping):
        for raw_name, value in tier_root.items():
            name = str(raw_name).strip().lower()
            if name not in _TIERS:
                _issue(
                    issues,
                    "model_tier_unknown",
                    f"model_tiers.{raw_name}",
                    "tier must be small, medium, or large",
                )
                continue
            entry = _parse_rich_entry(
                name=name,
                value=value,
                kind="tier",
                scope=_entry_scope(
                    name=str(raw_name), root="model_tiers", managed_config=managed
                ),
                path=f"model_tiers.{raw_name}",
                issues=issues,
            )
            if entry is not None:
                tiers[name] = entry

    aliases: dict[str, ConfiguredModelEntry] = {}
    legacy_aliases = model_section.get("aliases")
    if isinstance(legacy_aliases, Mapping):
        managed_legacy = (
            managed_model.get("aliases")
            if isinstance(managed_model, Mapping)
            else None
        )
        for name, value in legacy_aliases.items():
            scope: Literal["profile", "managed"] = (
                "managed"
                if isinstance(managed_legacy, Mapping) and name in managed_legacy
                else "profile"
            )
            entry = _legacy_alias_entry(
                name=name,
                value=value,
                current_provider=active_provider,
                scope=scope,
                issues=issues,
            )
            if entry is not None:
                aliases[entry.name] = entry

    alias_root = merged.get("model_aliases")
    if alias_root is not None and not isinstance(alias_root, Mapping):
        _issue(issues, "model_aliases_invalid", "model_aliases", "model_aliases must be a mapping")
    elif isinstance(alias_root, Mapping):
        if len(alias_root) > _MAX_ALIASES:
            _issue(
                issues,
                "model_aliases_too_many",
                "model_aliases",
                "model_aliases exceeds the entry limit",
            )
        else:
            for name, value in alias_root.items():
                entry = _parse_rich_entry(
                    name=name,
                    value=value,
                    kind="configured_alias",
                    scope=_entry_scope(
                        name=str(name), root="model_aliases", managed_config=managed
                    ),
                    path=f"model_aliases.{name}",
                    issues=issues,
                )
                if entry is not None:
                    aliases[entry.name] = entry

    issue_tuple = tuple(
        sorted(issues, key=lambda issue: (issue.path, issue.code, issue.message))
    )
    payload = _fingerprint_payload(
        tiers=tiers,
        aliases=aliases,
        active_provider=active_provider,
        active_provider_scope=active_provider_scope,
        active_base_url=active_base_url,
        active_api_mode=active_api_mode,
        issues=issue_tuple,
    )
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return WorkflowModelConfigSnapshot(
        tiers=tiers,
        aliases=aliases,
        active_provider=active_provider,
        active_provider_scope=active_provider_scope,
        active_base_url=active_base_url,
        active_api_mode=active_api_mode,
        config_fingerprint=fingerprint,
        issues=issue_tuple,
    )


def _read_config_file(path: Path | str | None) -> dict[str, Any]:
    if path is None:
        return {}
    source = Path(path)
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ModelResolutionError("model_config_unreadable", "model config is unreadable") from exc
    if len(raw) > _MAX_CONFIG_BYTES:
        raise ModelResolutionError("model_config_too_large", "model config exceeds byte limit")
    try:
        parsed = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ModelResolutionError("model_config_invalid", "model config YAML is invalid") from exc
    if not isinstance(parsed, dict):
        raise ModelResolutionError("model_config_invalid", "model config must be a mapping")
    return parsed


def load_workflow_model_config_snapshot(
    profile_config_path: Path | str,
    managed_config_path: Path | str | None = None,
) -> WorkflowModelConfigSnapshot:
    """Read exact bounded YAML files without environment expansion."""

    return parse_workflow_model_config(
        _read_config_file(profile_config_path),
        managed_config=_read_config_file(managed_config_path),
    )


def _concrete_provider(*candidates: object) -> str:
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        provider = candidate.strip()
        if provider and provider.lower() != "auto":
            return provider
    return ""


def resolve_workflow_model_reference(
    snapshot: WorkflowModelConfigSnapshot,
    requested_reference: str,
    *,
    node_provider: str | None = None,
    workflow_provider: str | None = None,
    node_options: Mapping[str, Any] | object | None = None,
    workflow_options: Mapping[str, Any] | object | None = None,
    catalog_source: str = "user",
) -> ResolvedModelRoute:
    """Resolve one authored reference without credentials, network, or catalogs."""

    if not isinstance(requested_reference, str) or not requested_reference.strip():
        raise ModelResolutionError("model_reference_invalid", "model reference is empty")
    reference = requested_reference.strip()
    entry: ConfiguredModelEntry | None
    if reference in _TIERS:
        kind: Literal["tier", "configured_alias", "literal"] = "tier"
        entry = snapshot.tiers.get(reference)
        if entry is None:
            raise ModelResolutionError(
                "model_tier_unconfigured", f"model tier {reference!r} is not configured"
            )
    elif reference.startswith("@"):
        kind = "configured_alias"
        alias = reference[1:].strip().lower()
        entry = snapshot.aliases.get(alias)
        if not alias or entry is None:
            raise ModelResolutionError(
                "model_alias_unconfigured", f"model alias {reference!r} is not configured"
            )
    else:
        kind = "literal"
        entry = None

    warnings: list[ResolutionDiagnostic] = []
    if entry is not None:
        provider = entry.provider
        model = entry.model
        config_scope = entry.config_scope
        explicit_providers = {
            value.strip()
            for value in (node_provider, workflow_provider)
            if isinstance(value, str)
            and value.strip()
            and value.strip().lower() != "auto"
        }
        if any(value != provider for value in explicit_providers):
            warnings.append(
                ResolutionDiagnostic(
                    code="model_reference_provider_overridden",
                    rationale="configured tier or alias provider overrides authored provider",
                )
            )
        defaults = dict(entry.options)
        base_url = entry.base_url
        if catalog_source in {"project", "showcase"} and config_scope == "profile":
            warnings.append(
                ResolutionDiagnostic(
                    code="model_reference_not_globally_portable",
                    rationale="package-distributed workflow uses profile-local model config",
                )
            )
    else:
        provider = _concrete_provider(
            node_provider, workflow_provider, snapshot.active_provider
        )
        if not provider:
            raise ModelResolutionError(
                "model_provider_unresolved",
                "literal model provider cannot be resolved without runtime credentials",
            )
        model = reference
        config_scope = snapshot.active_provider_scope
        defaults = {}
        base_url = (
            snapshot.active_base_url
            if provider == snapshot.active_provider
            else ""
        )

    merged_options: dict[str, Any] = dict(defaults)
    for values in (workflow_options, node_options):
        if values is None:
            continue
        if not isinstance(values, Mapping):
            raise ModelResolutionError(
                "model_reference_options_invalid", "model options must be mappings"
            )
        merged_options.update(values)
    options = _parse_options(merged_options)
    if options is None:
        raise ModelResolutionError(
            "model_reference_options_invalid", "model options are invalid"
        )

    from hermes_cli.runtime_provider import classify_execution_runtime

    runtime = classify_execution_runtime(
        provider=provider,
        model_config={"provider": provider, "default": model},
        provider_config={
            **({"base_url": base_url} if base_url else {}),
            **(
                {"api_mode": snapshot.active_api_mode}
                if entry is None
                and provider == snapshot.active_provider
                and snapshot.active_api_mode
                else {}
            ),
        },
    )
    route_material = {
        "resolver_version": 1,
        "requested_reference_sha256": hashlib.sha256(
            reference.encode("utf-8")
        ).hexdigest(),
        "reference_kind": kind,
        "provider": provider,
        "model": model,
        "api_mode": runtime.api_mode,
        "base_url_trust_class": runtime.base_url_trust_class,
        "base_url_sha256": (
            hashlib.sha256(base_url.encode("utf-8")).hexdigest()
            if base_url
            else ""
        ),
        "registration_provenance_digest": runtime.registration_provenance_digest,
        "provider_options": _thaw(options),
        "config_scope": config_scope,
        "config_fingerprint": snapshot.config_fingerprint,
        "warnings": [warning.code for warning in warnings],
    }
    route_fingerprint = hashlib.sha256(
        json.dumps(route_material, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return ResolvedModelRoute(
        requested_reference=reference,
        reference_kind=kind,
        provider=provider,
        model=model,
        api_mode=runtime.api_mode,
        route_fingerprint=route_fingerprint,
        registration_provenance_digest=runtime.registration_provenance_digest,
        provider_options=options,
        config_scope=config_scope,
        base_url_trust_class=runtime.base_url_trust_class,
        warnings=tuple(warnings),
    )
