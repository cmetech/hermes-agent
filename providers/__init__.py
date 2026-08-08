"""Provider module registry.

Provider profiles can live in two places:

1. Bundled plugins: ``plugins/model-providers/<name>/`` (shipped with hermes-agent)
2. User plugins: ``$HERMES_HOME/plugins/model-providers/<name>/``

Each plugin directory contains:
  - ``__init__.py`` — calls ``register_provider(profile)`` at import
  - ``plugin.yaml`` — manifest (name, kind: model-provider, version, description)

Discovery is lazy: the first call to ``get_provider_profile()`` or
``list_providers()`` scans both locations and imports every plugin. Name
collisions have deterministic precedence: bundled < legacy-compatible < user.
The loader, rather than profile-authored data, records registration provenance.

For backward compatibility, ``providers/*.py`` files (other than ``base.py``
and ``__init__.py``) are still discovered via ``pkgutil.iter_modules``.
This lets out-of-tree users drop a single-file profile into an editable
install without the plugin dir structure. New profiles should prefer the
plugin layout.

Usage::

    from providers import get_provider_profile
    profile = get_provider_profile("nvidia")   # ProviderProfile or None
    profile = get_provider_profile("kimi")     # checks name + aliases
"""

from __future__ import annotations

import importlib
import importlib.util
import hashlib
import inspect
import logging
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any

import yaml

from providers.base import OMIT_TEMPERATURE, ProviderProfile  # noqa: F401

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, ProviderProfile] = {}
_PROVIDER_LIST_CACHE: list[ProviderProfile] | None = None
_discovered = False

_MAX_CLOSURE_FILES = 256
_MAX_CLOSURE_BYTES = 4 * 1024 * 1024
_MAX_COLLISION_DIAGNOSTICS = 128
_ORIGIN_PRECEDENCE = {
    "bundled": 0,
    "legacy_compatible": 1,
    "user_plugin": 2,
}


@dataclass(frozen=True)
class ProviderRegistrationProvenance:
    """Loader-authored identity for a provider profile implementation."""

    origin_kind: str
    distribution_id: str
    distribution_version: str
    code_closure_digest: str
    code_closure_complete: bool


@dataclass(frozen=True)
class _ProviderAliasRegistration:
    canonical_name: str
    provenance: ProviderRegistrationProvenance


_ALIASES: dict[str, _ProviderAliasRegistration] = {}


@dataclass(frozen=True)
class ProviderRegistration:
    """A profile paired with immutable loader-authored provenance."""

    profile: ProviderProfile
    provenance: ProviderRegistrationProvenance


@dataclass(frozen=True)
class ProviderRegistrationCollision:
    """Bounded, path-free diagnostic for a duplicate provider name."""

    provider: str
    code: str


@dataclass(frozen=True)
class _RegistrationContext:
    origin_kind: str
    distribution_id: str
    distribution_version: str
    package_root: Path | None


_REGISTRATIONS: dict[str, ProviderRegistration] = {}
_REGISTRATION_COLLISIONS: list[ProviderRegistrationCollision] = []
_REGISTRATION_CONTEXT: ContextVar[_RegistrationContext | None] = ContextVar(
    "provider_registration_context",
    default=None,
)

# Repo-root ``plugins/model-providers/`` — populated at discovery time.
_BUNDLED_PLUGINS_DIR = (
    Path(__file__).resolve().parent.parent / "plugins" / "model-providers"
)


def _bounded_source_closure(
    *,
    profile: ProviderProfile,
    package_root: Path | None,
    caller_frame: FrameType | None,
) -> tuple[str, bool]:
    """Hash provider-owned source without exposing local paths.

    A plugin package contributes every Python file plus its manifest. Direct
    legacy registration contributes the executing source file. Provider class
    methods may also pull in repo-local helper functions, so their source files
    are included recursively when inspectable. Any unreadable or over-bound
    closure is ineligible rather than partially trusted.
    """

    sources: dict[str, Path] = {}
    if package_root is not None:
        try:
            root = package_root.resolve(strict=True)
            candidates = sorted(root.rglob("*.py"))
            manifest = root / "plugin.yaml"
            if manifest.is_file():
                candidates.append(manifest)
            for path in candidates:
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(root) or not resolved.is_file():
                    return "", False
                sources[f"package/{resolved.relative_to(root).as_posix()}"] = resolved
        except (OSError, RuntimeError, ValueError):
            return "", False
    else:
        filename = caller_frame.f_code.co_filename if caller_frame is not None else ""
        if not filename or filename.startswith("<"):
            return "", False
        try:
            path = Path(filename).resolve(strict=True)
        except (OSError, RuntimeError):
            return "", False
        if not path.is_file() or path.suffix != ".py":
            return "", False
        module_name = caller_frame.f_globals.get("__name__", "legacy")
        sources[f"module/{module_name}"] = path

    pending: list[Any] = []
    for value in vars(type(profile)).values():
        if inspect.isfunction(value):
            pending.append(value)
    inspected: set[int] = set()
    while pending:
        function = pending.pop()
        if id(function) in inspected:
            continue
        inspected.add(id(function))
        try:
            source_file = inspect.getsourcefile(function)
            if source_file and not source_file.startswith("<"):
                source_path = Path(source_file).resolve(strict=True)
                if source_path.suffix == ".py" and source_path.is_file():
                    module_name = getattr(function, "__module__", "provider-helper")
                    sources.setdefault(f"module/{module_name}", source_path)
            code = function.__code__
            globals_map = function.__globals__
        except (AttributeError, OSError, RuntimeError, TypeError):
            return "", False
        for name in code.co_names:
            referenced = globals_map.get(name)
            if inspect.isfunction(referenced):
                pending.append(referenced)
            elif inspect.isclass(referenced):
                pending.extend(
                    member
                    for member in vars(referenced).values()
                    if inspect.isfunction(member)
                )

    if not sources or len(sources) > _MAX_CLOSURE_FILES:
        return "", False
    digest = hashlib.sha256()
    total_bytes = 0
    try:
        for label, path in sorted(sources.items()):
            data = path.read_bytes()
            total_bytes += len(data)
            if total_bytes > _MAX_CLOSURE_BYTES:
                return "", False
            label_bytes = label.encode("utf-8")
            digest.update(len(label_bytes).to_bytes(4, "big"))
            digest.update(label_bytes)
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
    except OSError:
        return "", False
    return digest.hexdigest(), True


def _manifest_identity(plugin_dir: Path) -> tuple[str, str]:
    """Return bounded manifest identity, falling back to the directory slug."""

    distribution_id = plugin_dir.name
    distribution_version = ""
    manifest = plugin_dir / "plugin.yaml"
    try:
        raw = manifest.read_bytes()
        if len(raw) > 64 * 1024:
            return distribution_id, distribution_version
        parsed = yaml.safe_load(raw) or {}
        if isinstance(parsed, dict):
            name = parsed.get("name")
            version = parsed.get("version")
            if isinstance(name, str) and name.strip():
                distribution_id = name.strip()[:200]
            if isinstance(version, (str, int, float)):
                distribution_version = str(version).strip()[:100]
    except (OSError, UnicodeError, yaml.YAMLError):
        pass
    return distribution_id, distribution_version


def _registration_provenance(
    profile: ProviderProfile,
    caller_frame: FrameType | None,
) -> ProviderRegistrationProvenance:
    context = _REGISTRATION_CONTEXT.get()
    if context is None:
        module_name = (
            caller_frame.f_globals.get("__name__", "legacy")
            if caller_frame is not None
            else "legacy"
        )
        context = _RegistrationContext(
            origin_kind="legacy_compatible",
            distribution_id=str(module_name)[:200],
            distribution_version="",
            package_root=None,
        )
    digest, complete = _bounded_source_closure(
        profile=profile,
        package_root=context.package_root,
        caller_frame=caller_frame,
    )
    return ProviderRegistrationProvenance(
        origin_kind=context.origin_kind,
        distribution_id=context.distribution_id,
        distribution_version=context.distribution_version,
        code_closure_digest=digest,
        code_closure_complete=complete,
    )


def _record_collision(provider: str, code: str) -> None:
    _REGISTRATION_COLLISIONS.append(
        ProviderRegistrationCollision(provider=provider[:200], code=code)
    )
    del _REGISTRATION_COLLISIONS[:-_MAX_COLLISION_DIAGNOSTICS]


def _current_registration(name: str) -> ProviderRegistration | None:
    registration = _REGISTRATIONS.get(name)
    if registration is None or _REGISTRY.get(name) is not registration.profile:
        return None
    return registration


def register_provider(profile: ProviderProfile) -> None:
    """Register a provider profile by name and aliases.

    Equal-precedence registrations preserve last-writer compatibility. Higher
    precedence wins across origins, so user plugins deterministically override
    legacy and bundled profiles regardless of incidental import order.
    """
    global _PROVIDER_LIST_CACHE
    caller_frame = inspect.currentframe()
    caller_frame = caller_frame.f_back if caller_frame is not None else None
    provenance = _registration_provenance(profile, caller_frame)
    registration = ProviderRegistration(profile=profile, provenance=provenance)
    previous = _current_registration(profile.name)
    if previous is not None:
        previous_rank = _ORIGIN_PRECEDENCE[previous.provenance.origin_kind]
        new_rank = _ORIGIN_PRECEDENCE[provenance.origin_kind]
        if new_rank < previous_rank:
            _record_collision(profile.name, "provider_registration_lower_precedence_ignored")
            return
        code = (
            "provider_registration_higher_precedence_replaced"
            if new_rank > previous_rank
            else "provider_registration_same_precedence_replaced"
        )
        _record_collision(profile.name, code)

    for alias, alias_registration in tuple(_ALIASES.items()):
        if alias_registration.canonical_name == profile.name:
            _ALIASES.pop(alias, None)

    if profile.name in _ALIASES:
        _ALIASES.pop(profile.name)
        _record_collision(profile.name, "provider_alias_displaced_by_canonical")

    _REGISTRY[profile.name] = profile
    _REGISTRATIONS[profile.name] = registration

    seen_aliases: set[str] = set()
    for alias in profile.aliases:
        if alias in seen_aliases:
            continue
        seen_aliases.add(alias)
        if alias in _REGISTRY:
            _record_collision(alias, "provider_alias_rejected_canonical")
            continue

        previous_alias = _ALIASES.get(alias)
        if previous_alias is not None:
            previous_rank = _ORIGIN_PRECEDENCE[
                previous_alias.provenance.origin_kind
            ]
            new_rank = _ORIGIN_PRECEDENCE[provenance.origin_kind]
            if new_rank < previous_rank:
                _record_collision(
                    alias,
                    "provider_alias_lower_precedence_ignored",
                )
                continue
            code = (
                "provider_alias_higher_precedence_replaced"
                if new_rank > previous_rank
                else "provider_alias_same_precedence_replaced"
            )
            _record_collision(alias, code)

        _ALIASES[alias] = _ProviderAliasRegistration(
            canonical_name=profile.name,
            provenance=provenance,
        )
    _PROVIDER_LIST_CACHE = None


def get_provider_registration(name: str) -> ProviderRegistration | None:
    """Return the current loader-authored registration by name or alias."""

    if not _discovered:
        _discover_providers()
    canonical = _current_registration(name)
    if canonical is not None:
        return canonical
    alias = _ALIASES.get(name)
    return _current_registration(alias.canonical_name) if alias else None


def list_provider_registration_collisions() -> list[ProviderRegistrationCollision]:
    """Return a copy of bounded, public-safe collision diagnostics."""

    return list(_REGISTRATION_COLLISIONS)


def get_provider_profile(name: str) -> ProviderProfile | None:
    """Look up a provider profile by name or alias.

    Returns None if the provider has no profile (falls back to generic).
    """
    if not _discovered:
        _discover_providers()
    profile = _REGISTRY.get(name)
    if profile is not None:
        return profile
    alias = _ALIASES.get(name)
    return _REGISTRY.get(alias.canonical_name) if alias else None


def list_providers() -> list[ProviderProfile]:
    """Return all registered provider profiles (one per canonical name)."""
    global _PROVIDER_LIST_CACHE
    if not _discovered:
        _discover_providers()
    if _PROVIDER_LIST_CACHE is not None:
        return list(_PROVIDER_LIST_CACHE)
    # Deduplicate defensive direct inserts; aliases never enter _REGISTRY.
    seen: set[int] = set()
    result: list[ProviderProfile] = []
    for profile in _REGISTRY.values():
        pid = id(profile)
        if pid not in seen:
            seen.add(pid)
            result.append(profile)
    _PROVIDER_LIST_CACHE = result
    return list(result)


def _user_plugins_dir() -> Path | None:
    """Return ``$HERMES_HOME/plugins/model-providers/`` if it exists."""
    try:
        from hermes_constants import get_hermes_home

        d = get_hermes_home() / "plugins" / "model-providers"
        return d if d.is_dir() else None
    except Exception:
        return None


def _import_plugin_dir(plugin_dir: Path, source: str) -> None:
    """Import a single plugin directory so it self-registers.

    ``source`` is "bundled" or "user" and is converted to loader-owned
    provenance for every registration performed by the module.
    """
    init_file = plugin_dir / "__init__.py"
    if not init_file.exists():
        return

    # Give bundled plugins a stable import path (``plugins.model_providers.<name>``)
    # so relative imports within the plugin work. User plugins load via
    # ``importlib.util.spec_from_file_location`` with a unique module name so
    # multiple HERMES_HOME profiles don't alias each other.
    safe_name = plugin_dir.name.replace("-", "_")
    if source == "bundled":
        module_name = f"plugins.model_providers.{safe_name}"
    else:
        module_name = f"_hermes_user_provider_{safe_name}"

    if module_name in sys.modules:
        return  # already imported

    try:
        distribution_id, distribution_version = _manifest_identity(plugin_dir)
        context = _RegistrationContext(
            origin_kind="bundled" if source == "bundled" else "user_plugin",
            distribution_id=distribution_id,
            distribution_version=distribution_version,
            package_root=plugin_dir,
        )
        spec = importlib.util.spec_from_file_location(
            module_name, init_file, submodule_search_locations=[str(plugin_dir)]
        )
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        token = _REGISTRATION_CONTEXT.set(context)
        try:
            spec.loader.exec_module(module)
        finally:
            _REGISTRATION_CONTEXT.reset(token)
    except Exception as exc:
        logger.warning(
            "Failed to load %s provider plugin %s: %s", source, plugin_dir.name, exc
        )
        sys.modules.pop(module_name, None)


def _discover_providers() -> None:
    """Populate the registry by importing every provider plugin.

    Order:
      1. Bundled plugins at ``<repo>/plugins/model-providers/<name>/``
      2. Legacy per-file modules at ``providers/<name>.py`` (back-compat)
      3. User plugins at ``$HERMES_HOME/plugins/model-providers/<name>/``

    Each step imports its plugins, which call ``register_provider()`` at
    module-level. Later steps win on name collision.
    """
    global _discovered
    if _discovered:
        return
    _discovered = True

    # 1. Bundled plugins — shipped with hermes-agent.
    if _BUNDLED_PLUGINS_DIR.is_dir():
        for child in sorted(_BUNDLED_PLUGINS_DIR.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            _import_plugin_dir(child, "bundled")

    # 2. Legacy single-file profiles at providers/<name>.py. Kept for
    #    back-compat — if someone drops a ``providers/foo.py`` into an
    #    editable install, it still works without the plugin layout.
    try:
        import pkgutil

        import providers as _pkg

        for _importer, modname, _ispkg in pkgutil.iter_modules(_pkg.__path__):
            if modname.startswith("_") or modname == "base":
                continue
            try:
                context = _RegistrationContext(
                    origin_kind="legacy_compatible",
                    distribution_id=f"providers.{modname}",
                    distribution_version="",
                    package_root=None,
                )
                token = _REGISTRATION_CONTEXT.set(context)
                try:
                    importlib.import_module(f"providers.{modname}")
                finally:
                    _REGISTRATION_CONTEXT.reset(token)
            except ImportError as exc:
                logger.warning(
                    "Failed to import legacy provider module %s: %s", modname, exc
                )
    except Exception:
        pass

    # 3. User plugins — highest-precedence loader-owned source.
    user_dir = _user_plugins_dir()
    if user_dir is not None:
        for child in sorted(user_dir.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            _import_plugin_dir(child, "user")
