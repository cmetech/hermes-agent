"""Deterministic explicit/project/profile workflow discovery."""

from __future__ import annotations

import hashlib
from pathlib import Path

from plugins.workflow.models import (
    ValidationIssue,
    WorkflowPackage,
    WorkflowValidationError,
)
from plugins.workflow.schema import load_workflow

_PARSE_CACHE: dict[
    tuple[str, str, int],
    tuple[
        tuple[tuple[int, int, str], tuple[bool, int, int, str]],
        WorkflowPackage,
    ],
] = {}
_PROFILE_STATE_DIRECTORIES = frozenset({"runs", ".staging", ".quarantine", ".locks"})


def clear_discovery_cache() -> None:
    _PARSE_CACHE.clear()


def _yaml_paths(
    location: Path,
    *,
    excluded_top_level: frozenset[str] = frozenset(),
) -> tuple[Path, ...]:
    if location.is_file():
        return (location,) if location.suffix.lower() in {".yaml", ".yml"} else ()
    if not location.is_dir():
        return ()
    paths = (
        path
        for path in location.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".yaml", ".yml"}
        and not path.name.endswith(".hermes.yaml")
        and (
            not excluded_top_level
            or path.relative_to(location).parts[0] not in excluded_top_level
        )
    )
    return tuple(sorted(paths, key=lambda item: item.resolve().as_posix()))


def _load_cached(path: Path, *, source: str, precedence: int) -> WorkflowPackage:
    resolved = path.resolve(strict=True)
    stat = resolved.stat()
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    workflow_identity = (stat.st_size, stat.st_mtime_ns, digest)
    companion = resolved.with_name(f"{resolved.stem}.hermes.yaml")
    if companion.is_file():
        companion_stat = companion.stat()
        companion_identity = (
            True,
            companion_stat.st_size,
            companion_stat.st_mtime_ns,
            hashlib.sha256(companion.read_bytes()).hexdigest(),
        )
    else:
        companion_identity = (False, 0, 0, "")
    signature = (workflow_identity, companion_identity)
    key = (str(resolved), source, precedence)
    cached = _PARSE_CACHE.get(key)
    if cached is not None and cached[0] == signature:
        return cached[1]
    package = load_workflow(resolved, source=source, precedence=precedence)
    _PARSE_CACHE[key] = (signature, package)
    return package


def discover_workflows(
    workdir: str | Path,
    hermes_home: str | Path,
    user_home: str | Path,
    *,
    explicit_path: str | Path | None = None,
) -> tuple[WorkflowPackage, ...]:
    """Discover workflows without creating directories or mutating profile state."""
    del (
        user_home
    )  # Reserved for portable resource resolution; never used for branded discovery.
    locations: list[tuple[str, int, Path]] = []
    if explicit_path is not None:
        locations.append(("explicit", 0, Path(explicit_path).expanduser()))
    locations.extend([
        ("project", 1, Path(workdir).expanduser() / ".hermes" / "workflows"),
        ("profile", 2, Path(hermes_home).expanduser() / "workflows"),
    ])
    selected: dict[str, WorkflowPackage] = {}
    for source, precedence, location in locations:
        scan_location = location
        if (
            source == "explicit"
            and location.is_dir()
            and (location / "workflows").is_dir()
        ):
            scan_location = location / "workflows"
        level: dict[str, WorkflowPackage] = {}
        for path in _yaml_paths(
            scan_location,
            excluded_top_level=(
                _PROFILE_STATE_DIRECTORIES if source == "profile" else frozenset()
            ),
        ):
            package = _load_cached(path, source=source, precedence=precedence)
            name = package.definition.name
            if name in level:
                raise WorkflowValidationError(
                    ValidationIssue(
                        path=str(path),
                        code="duplicate_workflow_name",
                        message=f"duplicate workflow name at {source} precedence: {name}",
                    )
                )
            level[name] = package
        for name, package in level.items():
            selected.setdefault(name, package)
    return tuple(selected[name] for name in sorted(selected))
