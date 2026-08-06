"""Digest-verified, evidence-backed demonstrations of the workflow runtime."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
from importlib import resources
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import secrets
import shutil
import stat
import tempfile
import threading
import time
from types import MappingProxyType
from typing import Iterator, Literal, Mapping

import yaml

from cron.jobs import create_job, list_jobs, use_cron_store
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.cli import _input_requirements, _runtime_config, _scheduler
from plugins.workflow.compilation import (
    WorkflowCatalogSnapshot,
    WorkflowCompilation,
    compile_workflow,
)
from plugins.workflow.compat import (
    CompatibilityFinding,
    CompatibilityLevel,
    CompatibilityReport,
    assess_compatibility,
)
from plugins.workflow.entitlement import (
    validate_showcase_ai_contract,
    verified_showcase_run_metadata,
)
from plugins.workflow.input_contract import (
    WorkflowInputContractError,
    workflow_input_declarations,
)
from plugins.workflow.machine_contract import operator_command_contract
from plugins.workflow.provenance import TriggerProvenance
from plugins.workflow.models import RunExecutionLimits, WorkflowPackage
from plugins.workflow.language import supports_phase4_semantics
from plugins.workflow.schema import (
    load_workflow,
    load_workflow_snapshot,
    parse_workflow_source_bytes,
)
from plugins.workflow.store import RunStore
from plugins.workflow.sanitize import (
    workflow_filename_components_are_distinct,
    workflow_input_names_are_portable,
)
from plugins.workflow.trust import (
    WorkflowRiskSummary,
    WorkflowResourceCapacityError,
    WorkflowResourceCacheMissError,
    WorkflowResourceReadBudget,
    WorkflowTrustError,
    build_risk_summary,
    compute_package_digest,
    preflight_execution,
)


_ALLOWED_CLAIMS = frozenset({
    "immutable-inputs", "parallel-fan-in", "approval-rework",
    "artifact-verification", "no-live-inventory", "persisted-retry",
    "typed-timeout", "process-cleanup",
    "scoped-extensions", "persistent-session", "local-mcp-cleanup",
    "explicit-schedule-consent", "one-shot-ownership", "cron-recovery",
    "operator-approval",
})
_FORBIDDEN_TEXT = (
    "get-ciminstance", "get-computerinfo", "wmic ", "system_profiler",
    "/proc/", "powershell", "invoke-webrequest", "http://", "https://",
    "resource exhaustion", "process flood", "corruption mode", "elevation",
)


class ShowcaseCatalogError(ValueError):
    """A bundled showcase failed closed catalog or digest validation."""


@dataclass(frozen=True)
class ShowcaseScenario:
    id: str
    display_name: str
    purpose: str
    bundle_version: str
    package_version: str
    workflow_path: str
    interaction_mode: str
    offline: bool
    requires_ai: bool
    requires_network: bool
    safety_class: str
    supported_platforms: tuple[str, ...]
    expected_checkpoints: tuple[str, ...]
    expected_terminal_outcomes: tuple[str, ...]
    expected_artifacts: tuple[str, ...]
    capability_claims: tuple[str, ...]
    limits: Mapping[str, int]
    cleanup_ownership: str
    package_digest: str
    verified_bundled_provenance: bool
    input_fixtures: Mapping[str, str]
    input_value_bindings: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class VerifiedShowcasePackage:
    scenario: ShowcaseScenario
    package: WorkflowPackage
    compilation: WorkflowCompilation
    package_digest: str
    bundle_digest: str


@dataclass(frozen=True, slots=True)
class _VerifiedShowcaseCacheEntry:
    root: Path
    tree_signature: tuple[tuple[object, ...], ...]
    packages: Mapping[str, VerifiedShowcasePackage]
    resource_bytes: Mapping[Path, bytes]
    resource_aliases: Mapping[Path, Path]


_VERIFIED_SHOWCASE_CACHE: dict[str, _VerifiedShowcaseCacheEntry] = {}
_VERIFIED_SHOWCASE_CACHE_LOCK = threading.Lock()
_VERIFIED_SHOWCASE_VERIFY_LOCK = threading.Lock()
_VERIFIED_SHOWCASE_CACHE_GENERATION = 0


@dataclass(frozen=True)
class ShowcaseClaimResult:
    capability: str
    outcome: Literal["passed", "failed", "skipped"]
    reason_code: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ShowcaseReport:
    schema_version: int
    showcase_id: str
    showcase_version: str
    bundle_digest: str
    run_id: str
    definition_digest: str
    terminal_outcome: str
    claims: tuple[ShowcaseClaimResult, ...]
    interactions: tuple[dict, ...]
    artifacts: tuple[dict, ...]
    cleanup: dict
    suggested_next: tuple[str, ...]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_verified_bytes(
    path: Path,
    read_budget: WorkflowResourceReadBudget | None = None,
    verified_bytes: dict[Path, bytes] | None = None,
) -> bytes:
    if path.is_symlink():
        raise ShowcaseCatalogError(f"showcase bundle symlink is forbidden: {path}")
    resolved = path.resolve(strict=True)
    if verified_bytes is not None:
        cached = verified_bytes.get(resolved)
        if cached is not None:
            return cached
    if read_budget is None:
        data = resolved.read_bytes()
    else:
        data = read_budget.read(resolved)
    if verified_bytes is not None:
        verified_bytes[resolved] = data
    return data


def _tree_entries(
    root: Path,
    read_budget: WorkflowResourceReadBudget | None = None,
) -> tuple[Path, ...]:
    if root.is_symlink():
        raise ShowcaseCatalogError(f"showcase bundle symlink is forbidden: {root}")
    entries: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as children:
            for child in children:
                if (
                    read_budget is not None
                    and len(entries) >= read_budget.max_files
                ):
                    raise WorkflowResourceCapacityError(
                        "showcase package entry limit exceeded"
                    )
                path = Path(child.path)
                entries.append(path)
                if child.is_dir(follow_symlinks=False):
                    pending.append(path)
    return tuple(sorted(entries))


def _tree_digest(
    root: Path,
    read_budget: WorkflowResourceReadBudget | None = None,
    verified_bytes: dict[Path, bytes] | None = None,
) -> str:
    digest = hashlib.sha256()
    for path in _tree_entries(root, read_budget):
        if path.is_symlink() or not path.is_file():
            if path.is_symlink():
                raise ShowcaseCatalogError(f"showcase bundle symlink is forbidden: {path}")
            continue
        relative = path.relative_to(root).as_posix()
        data = _read_verified_bytes(path, read_budget, verified_bytes)
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(str(len(data)).encode())
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_package_safety(
    root: Path,
    scenario_id: str,
    read_budget: WorkflowResourceReadBudget | None = None,
    verified_bytes: dict[Path, bytes] | None = None,
) -> None:
    for path in _tree_entries(root, read_budget):
        if path.is_symlink():
            raise ShowcaseCatalogError(
                f"showcase safety contract rejects symlink: {scenario_id}"
            )
        if not path.is_file():
            continue
        try:
            text = _read_verified_bytes(
                path, read_budget, verified_bytes
            ).decode("utf-8").lower()
        except UnicodeError as exc:
            raise ShowcaseCatalogError(
                f"showcase safety contract rejects binary resource: {scenario_id}"
            ) from exc
        if any(token in text for token in _FORBIDDEN_TEXT):
            raise ShowcaseCatalogError(
                f"showcase safety contract rejected package bytes: {scenario_id}"
            )


@contextmanager
def _bundle_path(explicit: Path | None = None) -> Iterator[Path]:
    if explicit is not None:
        yield explicit.resolve()
        return
    candidate = resources.files("plugins.workflow").joinpath("showcases")
    with resources.as_file(candidate) as materialized:
        yield Path(materialized).resolve()


def _contained(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ShowcaseCatalogError(f"showcase path escapes bundle: {relative}")
    ancestor = root
    for part in path.parts:
        ancestor /= part
        if ancestor.is_symlink():
            raise ShowcaseCatalogError(f"showcase path is a symlink: {relative}")
    candidate = root / path
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ShowcaseCatalogError(f"showcase resource is missing or escapes: {relative}") from exc
    return candidate


def _fixture_path(package_root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative or len(relative) > 1024:
        raise ShowcaseCatalogError("showcase fixture path must be bounded text")
    parts = relative.split("/")
    portable = PurePosixPath(relative)
    if (
        portable.is_absolute()
        or "\\" in relative
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ShowcaseCatalogError(f"showcase fixture path is unsafe: {relative}")
    fixture = _contained(package_root, relative)
    try:
        metadata = fixture.lstat()
    except OSError as exc:
        raise ShowcaseCatalogError(
            f"showcase fixture resource is unreadable: {relative}"
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ShowcaseCatalogError(
            f"showcase fixture resource is not a regular file: {relative}"
        )
    return fixture


def _authenticated_input_mappings(
    item: Mapping[object, object],
    *,
    package: WorkflowPackage,
) -> tuple[dict[str, str], dict[str, str]]:
    raw_fixtures = item.get("input_fixtures", {})
    raw_bindings = item.get("input_value_bindings", {})
    if (
        not isinstance(raw_fixtures, Mapping)
        or not isinstance(raw_bindings, Mapping)
        or len(raw_fixtures) > 64
        or len(raw_bindings) > 64
    ):
        raise ShowcaseCatalogError(
            "showcase input fixtures and bindings must be bounded mappings"
        )
    if not workflow_input_names_are_portable(raw_fixtures) or not (
        workflow_input_names_are_portable(raw_bindings)
    ):
        raise ShowcaseCatalogError("showcase input names must be portable")

    fixtures: dict[str, str] = {}
    bindings: dict[str, str] = {}
    for raw_name, raw_path in raw_fixtures.items():
        if not isinstance(raw_name, str) or not isinstance(raw_path, str):
            raise ShowcaseCatalogError("showcase fixture mapping is invalid")
        _fixture_path(package.root, raw_path)
        fixtures[raw_name] = raw_path
    for raw_public, raw_target in raw_bindings.items():
        if not isinstance(raw_public, str) or not isinstance(raw_target, str):
            raise ShowcaseCatalogError("showcase input binding is invalid")
        bindings[raw_public] = raw_target

    try:
        declarations = workflow_input_declarations(package)
    except WorkflowInputContractError as exc:
        raise ShowcaseCatalogError("showcase input declarations are invalid") from exc

    targets = list(bindings.values())
    if not workflow_input_names_are_portable(targets):
        raise ShowcaseCatalogError("showcase input binding targets must be one-to-one")
    fixture_targets = {name.casefold() for name in fixtures}
    binding_targets = {target.casefold() for target in targets}
    public_sources = {name.casefold() for name in bindings}
    if fixture_targets & binding_targets:
        raise ShowcaseCatalogError("showcase binding and fixture targets overlap")
    if public_sources & {name.casefold() for name in declarations}:
        raise ShowcaseCatalogError(
            "showcase public input names must not alias internal targets"
        )
    if not workflow_filename_components_are_distinct(
        [
            *fixtures,
            *(
                f"{name}.txt"
                for name, declaration in declarations.items()
                if declaration.accepts_api_value
            ),
        ]
    ):
        raise ShowcaseCatalogError("showcase input targets collide after staging")
    if len({path.casefold() for path in fixtures.values()}) != len(fixtures):
        raise ShowcaseCatalogError("showcase fixture paths must be one-to-one")

    for target in targets:
        declaration = declarations.get(target)
        if declaration is None:
            raise ShowcaseCatalogError(
                f"showcase binding target is undeclared: {target}"
            )
        if declaration.kind != "text":
            raise ShowcaseCatalogError(
                f"showcase binding target must declare text kind: {target}"
            )
    for target in fixtures:
        declaration = declarations.get(target)
        if declaration is None:
            raise ShowcaseCatalogError(
                f"showcase fixture target is undeclared: {target}"
            )
        if declaration.kind != "file":
            raise ShowcaseCatalogError(
                f"showcase fixture target must declare file kind: {target}"
            )
    return dict(sorted(fixtures.items())), dict(sorted(bindings.items()))


def _bundle_digest(
    root: Path,
    read_budget: WorkflowResourceReadBudget | None = None,
) -> str:
    return _sha256(
        _read_verified_bytes(root / "catalog.yaml", read_budget)
        + b"\0"
        + _read_verified_bytes(root / "digests.json", read_budget)
    )


def _bundle_tree_signature(
    root: Path,
    read_budget: WorkflowResourceReadBudget,
) -> tuple[tuple[object, ...], ...]:
    packages = root / "packages"
    signature: list[tuple[object, ...]] = []
    for path in _tree_entries(packages, read_budget):
        metadata = path.lstat()
        signature.append(
            (
                path.relative_to(packages).as_posix(),
                path.is_file(),
                path.is_dir(),
                path.is_symlink(),
                getattr(metadata, "st_dev", None),
                getattr(metadata, "st_ino", None),
                metadata.st_size,
                getattr(metadata, "st_mtime_ns", None),
                getattr(metadata, "st_ctime_ns", None),
            )
        )
    if any(item[-2] is None or item[-1] is None for item in signature):
        return ()
    return tuple(signature)


def _fresh_read_budget(
    read_budget: WorkflowResourceReadBudget | None,
) -> WorkflowResourceReadBudget | None:
    if read_budget is None:
        return None
    return WorkflowResourceReadBudget(
        max_file_bytes=read_budget.max_file_bytes,
        max_total_bytes=read_budget.max_total_bytes,
        max_files=read_budget.max_files,
    )


def _charge_read_budget(
    read_budget: WorkflowResourceReadBudget,
    additional: WorkflowResourceReadBudget,
) -> None:
    if (
        read_budget.bytes_read + additional.bytes_read
        > read_budget.max_total_bytes
        or read_budget.files_read + additional.files_read > read_budget.max_files
    ):
        raise WorkflowResourceCapacityError(
            "showcase verification aggregate limit exceeded"
        )
    read_budget.bytes_read += additional.bytes_read
    read_budget.files_read += additional.files_read


def load_showcase_catalog(
    bundle_root: Path | None = None,
    *,
    read_budget: WorkflowResourceReadBudget | None = None,
    allow_repair: bool = True,
) -> dict[str, ShowcaseScenario]:
    with _bundle_path(bundle_root) as root:
        verified_bytes = {} if read_budget is None else None
        try:
            catalog_bytes = _read_verified_bytes(root / "catalog.yaml", read_budget)
            digest_bytes = _read_verified_bytes(root / "digests.json", read_budget)
            raw = yaml.safe_load(catalog_bytes.decode("utf-8"))
            manifest = json.loads(digest_bytes.decode("utf-8"))
        except (OSError, ValueError, yaml.YAMLError, json.JSONDecodeError) as exc:
            raise ShowcaseCatalogError(f"invalid showcase bundle metadata: {exc}") from exc
        if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
            raise ShowcaseCatalogError("unsupported showcase catalog schema")
        if not isinstance(manifest, Mapping) or manifest.get("schema_version") != 1:
            raise ShowcaseCatalogError("unsupported showcase digest schema")
        catalog_digest = manifest.get("catalog_sha256")
        if catalog_digest != _sha256(catalog_bytes):
            if bundle_root is None and allow_repair:
                from hermes_cli.capability_staging import (
                    repair_authenticated_resource_checkout,
                )

                if repair_authenticated_resource_checkout(root):
                    repaired = load_showcase_catalog(
                        root,
                        read_budget=_fresh_read_budget(read_budget),
                        allow_repair=False,
                    )
                    return {
                        key: replace(value, verified_bundled_provenance=True)
                        for key, value in repaired.items()
                    }
            raise ShowcaseCatalogError("showcase catalog digest mismatch")
        package_digests = manifest.get("packages")
        if not isinstance(package_digests, Mapping):
            raise ShowcaseCatalogError("showcase package digest manifest is incomplete")
        scenarios = raw.get("scenarios")
        if not isinstance(scenarios, list):
            raise ShowcaseCatalogError("showcase scenarios must be a list")
        result: dict[str, ShowcaseScenario] = {}
        required = {
            "id", "display_name", "purpose", "bundle_version", "package_version",
            "workflow_path", "interaction_mode", "offline", "requires_ai",
            "requires_network", "safety_class", "supported_platforms",
            "expected_checkpoints", "expected_terminal_outcomes", "expected_artifacts",
            "capability_claims", "limits", "cleanup_ownership",
        }
        for item in scenarios:
            if not isinstance(item, Mapping) or not required <= set(item):
                raise ShowcaseCatalogError("showcase scenario is missing required fields")
            scenario_id = str(item["id"])
            if scenario_id in result:
                raise ShowcaseCatalogError(f"duplicate showcase id: {scenario_id}")
            workflow_path = str(item["workflow_path"])
            workflow = _contained(root, workflow_path)
            package_root = workflow.parent.parent
            _validate_package_safety(
                package_root, scenario_id, read_budget, verified_bytes
            )
            actual_digest = _tree_digest(
                package_root, read_budget, verified_bytes
            )
            if package_digests.get(scenario_id) != actual_digest:
                raise ShowcaseCatalogError(f"showcase package digest mismatch: {scenario_id}")
            claims = tuple(str(value) for value in item["capability_claims"])
            limits = item["limits"]
            if not isinstance(limits, Mapping) or not isinstance(limits.get("wall_seconds"), int):
                raise ShowcaseCatalogError(f"showcase limits are invalid: {scenario_id}")
            text = json.dumps(item, sort_keys=True).lower()
            if (
                not claims or not set(claims) <= _ALLOWED_CLAIMS
                or "destructive" in str(item["safety_class"]).lower()
                or bool(item["requires_network"])
                or int(limits["wall_seconds"]) > (600 if item["requires_ai"] else 300)
                or any(token in text for token in _FORBIDDEN_TEXT)
            ):
                raise ShowcaseCatalogError(f"showcase safety contract rejected: {scenario_id}")
            package = _load_workflow_from_verified_bytes(
                workflow,
                read_budget=read_budget,
                verified_bytes=verified_bytes,
            )
            validate_showcase_ai_contract(
                scenario_id,
                requires_ai=bool(item["requires_ai"]),
                definition=package.definition,
            )
            input_fixtures, input_value_bindings = _authenticated_input_mappings(
                item,
                package=package,
            )
            result[scenario_id] = ShowcaseScenario(
                **{key: str(item[key]) for key in ("id", "display_name", "purpose", "bundle_version", "package_version", "workflow_path", "interaction_mode", "safety_class", "cleanup_ownership")},
                offline=bool(item["offline"]), requires_ai=bool(item["requires_ai"]),
                requires_network=bool(item["requires_network"]),
                supported_platforms=tuple(map(str, item["supported_platforms"])),
                expected_checkpoints=tuple(map(str, item["expected_checkpoints"])),
                expected_terminal_outcomes=tuple(map(str, item["expected_terminal_outcomes"])),
                expected_artifacts=tuple(map(str, item["expected_artifacts"])),
                capability_claims=claims,
                limits={str(key): int(value) for key, value in limits.items()},
                package_digest=actual_digest,
                verified_bundled_provenance=bundle_root is None,
                input_fixtures=input_fixtures,
                input_value_bindings=input_value_bindings,
            )
        return dict(sorted(result.items()))


def _load_workflow_from_verified_bytes(
    workflow: Path,
    *,
    read_budget: WorkflowResourceReadBudget | None,
    verified_bytes: dict[Path, bytes] | None = None,
    source: str = "explicit",
    precedence: int = 0,
) -> WorkflowPackage:
    if read_budget is None:
        authenticated_snapshot = verified_bytes is not None
        workflow_key = workflow.absolute()
        if authenticated_snapshot:
            try:
                workflow_bytes = verified_bytes[workflow_key]
            except KeyError as exc:
                raise ShowcaseCatalogError(
                    f"verified workflow bytes are unavailable: {workflow.name}"
                ) from exc
        else:
            verified_bytes = {}
            try:
                workflow_bytes = _read_verified_bytes(
                    workflow, verified_bytes=verified_bytes
                )
            except OSError as exc:
                raise ShowcaseCatalogError(
                    f"verified workflow bytes are unavailable: {workflow.name}"
                ) from exc
        sidecar_key = workflow_key.with_name(f"{workflow.stem}.hermes.yaml")
        if authenticated_snapshot:
            try:
                sidecar_bytes = verified_bytes[sidecar_key]
            except KeyError as exc:
                raise ShowcaseCatalogError(
                    f"verified workflow sidecar bytes are unavailable: {workflow.name}"
                ) from exc
        else:
            try:
                sidecar_bytes = _read_verified_bytes(
                    sidecar_key, verified_bytes=verified_bytes
                )
            except FileNotFoundError:
                sidecar_bytes = None
        return load_workflow_snapshot(
            workflow,
            workflow_bytes=workflow_bytes,
            sidecar_bytes=sidecar_bytes,
            source=source,
            precedence=precedence,
        )
    try:
        workflow_bytes = read_budget.read_cached(workflow)
    except WorkflowResourceCacheMissError as exc:
        raise ShowcaseCatalogError(
            f"verified workflow bytes are unavailable: {workflow.name}"
        ) from exc
    sidecar = workflow.with_name(f"{workflow.stem}.hermes.yaml")
    try:
        sidecar_bytes = read_budget.read_cached(sidecar)
    except WorkflowResourceCacheMissError:
        sidecar_bytes = None
    return load_workflow_snapshot(
        workflow,
        workflow_bytes=workflow_bytes,
        sidecar_bytes=sidecar_bytes,
        source=source,
        precedence=precedence,
    )


def _scenario_package(
    scenario: ShowcaseScenario,
    *,
    source: str = "explicit",
    precedence: int = 0,
    bundle_root: Path | None = None,
    read_budget: WorkflowResourceReadBudget | None = None,
) -> WorkflowPackage:
    if bundle_root is not None:
        return _load_workflow_from_verified_bytes(
            _contained(bundle_root, scenario.workflow_path),
            read_budget=read_budget,
            source=source,
            precedence=precedence,
        )
    with _bundle_path() as root:
        return _load_workflow_from_verified_bytes(
            _contained(root, scenario.workflow_path),
            read_budget=read_budget,
            source=source,
            precedence=precedence,
        )


def _compile_showcase_package(
    package: WorkflowPackage,
    *,
    read_budget: WorkflowResourceReadBudget | None = None,
    catalog_packages: tuple[WorkflowPackage, ...] = (),
) -> WorkflowCompilation:
    def source_document(candidate: WorkflowPackage):
        workflow_bytes = (
            read_budget.read_cached(candidate.workflow_path)
            if read_budget is not None
            else candidate.workflow_path.read_bytes()
        )
        sidecar_bytes = None
        if candidate.sidecar_path is not None:
            sidecar_bytes = (
                read_budget.read_cached(candidate.sidecar_path)
                if read_budget is not None
                else candidate.sidecar_path.read_bytes()
            )
        return parse_workflow_source_bytes(
            candidate.workflow_path,
            workflow_bytes=workflow_bytes,
            sidecar_bytes=sidecar_bytes,
            source=candidate.source,
            precedence=candidate.precedence,
        )

    candidates = catalog_packages or (package,)
    sources = tuple(source_document(candidate) for candidate in candidates)
    root = next(
        source
        for source in sources
        if source.workflow_path == package.workflow_path
        and source.source == package.source
        and source.precedence == package.precedence
    )
    return compile_workflow(root, WorkflowCatalogSnapshot.capture(sources))


@contextmanager
def _scenario_compilation(
    scenario: ShowcaseScenario,
) -> Iterator[WorkflowCompilation]:
    """Keep materialized distribution paths alive through admission preparation."""
    catalog = load_showcase_catalog()
    with _bundle_path() as root:
        packages_by_id = {
            candidate_id: _scenario_package(candidate, bundle_root=root)
            for candidate_id, candidate in catalog.items()
        }
        yield _compile_showcase_package(
            packages_by_id[scenario.id],
            catalog_packages=tuple(packages_by_id.values()),
        )


def _verified_distribution_risk(
    scenario: ShowcaseScenario,
    package: WorkflowPackage,
    read_budget: WorkflowResourceReadBudget | None = None,
    *,
    enforce_runnable: bool = True,
    compilation: WorkflowCompilation | None = None,
) -> WorkflowRiskSummary:
    risk, _compatibility = _verified_distribution_assessment(
        scenario,
        package,
        read_budget,
        enforce_runnable=enforce_runnable,
        compilation=compilation,
    )
    return risk


def _verified_distribution_assessment(
    scenario: ShowcaseScenario,
    package: WorkflowPackage,
    read_budget: WorkflowResourceReadBudget | None = None,
    *,
    enforce_runnable: bool = True,
    compilation: WorkflowCompilation | None = None,
) -> tuple[WorkflowRiskSummary, CompatibilityReport]:
    if not scenario.verified_bundled_provenance:
        raise ShowcaseCatalogError(
            "showcase lacks verified bundled distribution provenance"
        )
    if _tree_digest(package.root, read_budget) != scenario.package_digest:
        raise ShowcaseCatalogError(
            f"showcase package changed after catalog verification: {scenario.id}"
        )
    compatibility = assess_compatibility(package)
    if enforce_runnable and not compatibility.runnable:
        raise ShowcaseCatalogError(
            f"showcase package is not runnable under ordinary compatibility policy: {scenario.id}"
        )
    risk = build_risk_summary(
        package,
        compatibility,
        read_budget=read_budget,
        compilation=(
            compilation
            if compilation is not None
            and supports_phase4_semantics(
                package.language.effective_profile,
                package.language.normalizer_version,
            )
            else None
        ),
    )
    try:
        preflight_execution(
            risk,
            trusted=scenario.verified_bundled_provenance,
        )
    except WorkflowTrustError as exc:
        if enforce_runnable:
            raise ShowcaseCatalogError(
                f"showcase package failed ordinary execution-risk policy: {exc}"
            ) from exc
        compatibility = CompatibilityReport(
            level=CompatibilityLevel.UNSUPPORTED,
            findings=(
                *compatibility.findings,
                CompatibilityFinding(
                    path="sidecar.execution_environment",
                    level=CompatibilityLevel.UNSUPPORTED,
                    message="workflow requires a configured isolated backend",
                    blocking=True,
                    code="execution_environment_unavailable",
                    effective_profile=package.language.effective_profile,
                ),
            ),
            runnable=False,
        )
    return risk, compatibility


def showcase_background_api_eligible(scenario: ShowcaseScenario) -> bool:
    """Return whether ordinary background admission preserves scenario intent."""
    return (
        scenario.interaction_mode == "guided"
        and not scenario.requires_network
    )


def _clear_verified_showcase_cache_for_tests() -> None:
    global _VERIFIED_SHOWCASE_CACHE_GENERATION
    with _VERIFIED_SHOWCASE_CACHE_LOCK:
        _VERIFIED_SHOWCASE_CACHE_GENERATION += 1
        _VERIFIED_SHOWCASE_CACHE.clear()


def _load_verified_showcase_cache_hit(
    *,
    resolved_root: Path,
    bundle_digest: str,
    read_budget: WorkflowResourceReadBudget,
) -> dict[str, VerifiedShowcasePackage] | None:
    global _VERIFIED_SHOWCASE_CACHE_GENERATION
    with _VERIFIED_SHOWCASE_CACHE_LOCK:
        cached = _VERIFIED_SHOWCASE_CACHE.get(bundle_digest)
        generation = _VERIFIED_SHOWCASE_CACHE_GENERATION
    if cached is None:
        return None

    tree_signature = _bundle_tree_signature(resolved_root, read_budget)
    cache_hit = False
    with _VERIFIED_SHOWCASE_CACHE_LOCK:
        if (
            generation != _VERIFIED_SHOWCASE_CACHE_GENERATION
            or _VERIFIED_SHOWCASE_CACHE.get(bundle_digest) is not cached
        ):
            return None
        if (
            tree_signature
            and cached.root == resolved_root
            and cached.tree_signature == tree_signature
        ):
            cache_hit = True
        else:
            _VERIFIED_SHOWCASE_CACHE_GENERATION += 1
            _VERIFIED_SHOWCASE_CACHE.clear()
    if cache_hit:
        previous_contents = dict(read_budget._contents)
        previous_aliases = dict(read_budget._aliases)
        previous_files_read = read_budget.files_read
        previous_bytes_read = read_budget.bytes_read
        try:
            _restore_cached_resources(read_budget, cached)
        except Exception:
            read_budget._contents.clear()
            read_budget._contents.update(previous_contents)
            read_budget._aliases.clear()
            read_budget._aliases.update(previous_aliases)
            read_budget.files_read = previous_files_read
            read_budget.bytes_read = previous_bytes_read
            raise
        with _VERIFIED_SHOWCASE_CACHE_LOCK:
            current = (
                generation == _VERIFIED_SHOWCASE_CACHE_GENERATION
                and _VERIFIED_SHOWCASE_CACHE.get(bundle_digest) is cached
            )
        if current:
            return dict(cached.packages)
        read_budget._contents.clear()
        read_budget._contents.update(previous_contents)
        read_budget._aliases.clear()
        read_budget._aliases.update(previous_aliases)
        read_budget.files_read = previous_files_read
        read_budget.bytes_read = previous_bytes_read
    return None


def _restore_cached_resources(
    read_budget: WorkflowResourceReadBudget,
    cached: _VerifiedShowcaseCacheEntry,
) -> None:
    """Copy authenticated immutable bytes into one request-owned read budget."""
    for path, data in cached.resource_bytes.items():
        existing = read_budget._contents.get(path)
        if existing is not None:
            if existing != data:
                raise ShowcaseCatalogError(
                    "showcase resource cache conflicts with authenticated bytes"
                )
            continue
        if (
            read_budget.files_read >= read_budget.max_files
            or len(data) > read_budget.max_file_bytes
            or read_budget.bytes_read + len(data) > read_budget.max_total_bytes
        ):
            raise WorkflowResourceCapacityError(
                "showcase verification aggregate limit exceeded"
            )
        read_budget._contents[path] = data
        read_budget.files_read += 1
        read_budget.bytes_read += len(data)
    read_budget._aliases.update(cached.resource_aliases)


def _verify_and_cache_showcase_packages(
    *,
    resolved_root: Path,
    bundle_digest: str,
    read_budget: WorkflowResourceReadBudget,
) -> dict[str, VerifiedShowcasePackage]:
    global _VERIFIED_SHOWCASE_CACHE_GENERATION
    before_signature = _bundle_tree_signature(resolved_root, read_budget)
    catalog = load_showcase_catalog(
        read_budget=read_budget,
        allow_repair=False,
    )
    verified: dict[str, VerifiedShowcasePackage] = {}
    loaded_packages: dict[str, WorkflowPackage] = {}
    for scenario_id, scenario in catalog.items():
        if not scenario.verified_bundled_provenance:
            raise ShowcaseCatalogError(
                "showcase lacks verified bundled distribution provenance"
            )
        loaded_packages[scenario_id] = _scenario_package(
            scenario,
            source="showcase",
            precedence=3,
            bundle_root=resolved_root,
            read_budget=read_budget,
        )
    catalog_packages = tuple(loaded_packages.values())
    for scenario_id, scenario in catalog.items():
        package = loaded_packages[scenario_id]
        compilation = _compile_showcase_package(
            package,
            read_budget=read_budget,
            catalog_packages=catalog_packages,
        )
        package = compilation.package
        phase4 = supports_phase4_semantics(
            package.language.effective_profile,
            package.language.normalizer_version,
        )
        verified[scenario_id] = VerifiedShowcasePackage(
            scenario=scenario,
            package=package,
            compilation=compilation,
            package_digest=compute_package_digest(
                package,
                read_budget=read_budget,
            ).sha256 if not phase4 else compilation.composite_digest,
            bundle_digest=bundle_digest,
        )

    after_signature = _bundle_tree_signature(resolved_root, read_budget)
    postcheck_budget = _fresh_read_budget(read_budget)
    assert postcheck_budget is not None
    current_bundle_digest = _bundle_digest(resolved_root, postcheck_budget)
    _charge_read_budget(read_budget, postcheck_budget)
    if (
        before_signature != after_signature
        or bundle_digest != current_bundle_digest
    ):
        raise ShowcaseCatalogError(
            "showcase bundle changed during verified loading"
        )
    if after_signature:
        with _VERIFIED_SHOWCASE_CACHE_LOCK:
            _VERIFIED_SHOWCASE_CACHE_GENERATION += 1
            _VERIFIED_SHOWCASE_CACHE.clear()
            _VERIFIED_SHOWCASE_CACHE[bundle_digest] = (
                _VerifiedShowcaseCacheEntry(
                    root=resolved_root,
                    tree_signature=after_signature,
                    packages=MappingProxyType(dict(verified)),
                    resource_bytes=MappingProxyType(dict(read_budget._contents)),
                    resource_aliases=MappingProxyType(dict(read_budget._aliases)),
                )
            )
    return verified


def load_verified_showcase_packages(
    *,
    read_budget: WorkflowResourceReadBudget,
    force_reverify: bool = False,
) -> dict[str, VerifiedShowcasePackage]:
    """Load only the authenticated bundled distribution, with bounded caching."""
    global _VERIFIED_SHOWCASE_CACHE_GENERATION
    if not isinstance(read_budget, WorkflowResourceReadBudget):
        raise TypeError("read_budget must be a WorkflowResourceReadBudget")
    with _bundle_path() as root:
        resolved_root = root.resolve(strict=True)
        bundle_digest = _bundle_digest(resolved_root, read_budget)
        if force_reverify:
            with _VERIFIED_SHOWCASE_CACHE_LOCK:
                _VERIFIED_SHOWCASE_CACHE_GENERATION += 1
                _VERIFIED_SHOWCASE_CACHE.clear()
        else:
            cached = _load_verified_showcase_cache_hit(
                resolved_root=resolved_root,
                bundle_digest=bundle_digest,
                read_budget=read_budget,
            )
            if cached is not None:
                return cached

        with _VERIFIED_SHOWCASE_VERIFY_LOCK:
            if not force_reverify:
                cached = _load_verified_showcase_cache_hit(
                    resolved_root=resolved_root,
                    bundle_digest=bundle_digest,
                    read_budget=read_budget,
                )
                if cached is not None:
                    return cached
            return _verify_and_cache_showcase_packages(
                resolved_root=resolved_root,
                bundle_digest=bundle_digest,
                read_budget=read_budget,
            )


def load_verified_showcase_package(
    showcase_id: str,
    *,
    read_budget: WorkflowResourceReadBudget,
    force_reverify: bool = False,
) -> VerifiedShowcasePackage:
    verified = load_verified_showcase_packages(
        read_budget=read_budget,
        force_reverify=force_reverify,
    )
    try:
        return verified[showcase_id]
    except KeyError as exc:
        raise ShowcaseCatalogError(f"unknown showcase: {showcase_id}") from exc


def preflight_showcase(showcase_id: str, *, hermes_home: str | Path) -> dict[str, object]:
    catalog = load_showcase_catalog()
    if showcase_id not in catalog:
        raise ShowcaseCatalogError(f"unknown showcase: {showcase_id}")
    scenario = catalog[showcase_id]
    package = _scenario_package(scenario)
    with _bundle_path() as root:
        bundle_digest = _bundle_digest(root)
    confirmation_kind = (
        "schedule" if scenario.interaction_mode == "schedule" else None
    )
    token = _sha256(f"{confirmation_kind or 'none'}\0{scenario.package_digest}\0{bundle_digest}".encode()) if confirmation_kind else None
    requested_skills = sorted({str(skill) for node in package.definition.nodes for skill in node.options.get("skills", ())})
    local_mcp = sorted({str(value) for node in package.definition.nodes for value in ((node.options.get("mcp"),) if isinstance(node.options.get("mcp"), str) else node.options.get("mcp", ())) if isinstance(value, str)})
    inline_agents = max(
        (len(node.options.get("agents", {})) for node in package.definition.nodes),
        default=0,
    )
    input_requirements = {
        item.name: asdict(item) for item in _input_requirements(package, [])
    }
    for public_name, internal_name in scenario.input_value_bindings.items():
        requirement = input_requirements.pop(internal_name)
        requirement["name"] = public_name
        input_requirements[public_name] = requirement
    return {
        "schema_version": 1, "showcase_id": scenario.id, "display_name": scenario.display_name,
        "purpose": scenario.purpose, "runnable": True, "offline": scenario.offline,
        "requires_ai": scenario.requires_ai, "requires_network": scenario.requires_network,
        "requires_confirmation": confirmation_kind is not None,
        "confirmation_kind": confirmation_kind, "confirmation_token": token,
        "package_digest": scenario.package_digest, "bundle_digest": bundle_digest,
        "requested_skills": requested_skills, "local_mcp_servers": [f"mcp/{value}" for value in local_mcp],
        "inline_agent_limit": inline_agents, "wall_seconds": scenario.limits["wall_seconds"],
        "input_requirements": [
            input_requirements[name] for name in sorted(input_requirements)
        ],
        "side_effects_initialized": False,
        "command_contract": operator_command_contract(),
    }


def _store(home: str | Path, package=None) -> tuple[RunStore, object]:
    config = _runtime_config(home, sidecar=package.sidecar if package is not None else None)
    store = RunStore(
        home, max_executing_runs=config.max_executing_runs,
        max_queued_runs=config.max_queued_runs, max_paused_runs=config.max_paused_runs,
        max_nonterminal_runs=config.max_nonterminal_runs,
        max_start_requests_per_minute=config.max_start_requests_per_minute,
        max_total_workers=min(4, config.max_total_workers), max_run_bytes=32 * 1024 * 1024,
    )
    return store, config


def _advance_until_wait(store: RunStore, config, run_id: str) -> dict[str, object]:
    from agent.plugin_agent import PluginAgentRunner

    scheduler = _scheduler(
        store,
        config,
        agent_runner=PluginAgentRunner(plugin_id="workflow"),
    )
    deadline = time.monotonic() + 305
    while True:
        projection = scheduler.advance(run_id)
        if projection["status"] != "waiting_retry":
            return store.get_run_status(run_id)
        if time.monotonic() >= deadline:
            return store.get_run_status(run_id)
        next_at = datetime.fromisoformat(str(store.get_run_status(run_id)["next_retry_at"]))
        time.sleep(max(0.01, min(1.0, (next_at - datetime.now(timezone.utc)).total_seconds())))


def _stage_fixture(home: Path) -> tuple[Path, Path]:
    staging_root = home / "workflow" / "showcase" / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="laptop-", dir=staging_root))
    (directory / "owner.json").write_text(json.dumps({"owner": "workflow-showcase"}), encoding="utf-8")
    with _bundle_path() as root:
        source = _contained(root, "packages/laptop-diagnostic/fixtures/laptop-snapshot.json")
        target = directory / "sanitized-evidence.json"
        target.write_bytes(source.read_bytes())
    return directory, target


def _schedule_showcase(scenario: ShowcaseScenario, *, home: Path, schedule_at: str, token: str | None) -> dict[str, object]:
    preflight = preflight_showcase(scenario.id, hermes_home=home)
    if token != preflight["confirmation_token"]:
        return {"status": "skipped", "reason_code": "schedule_confirmation_required", "run_id": None, "confirmation_token": preflight["confirmation_token"]}
    with use_cron_store(home):
        job = create_job(
            prompt="Run `hermes workflow showcase run scheduling --json` as the explicitly authorized one-shot showcase.",
            schedule=schedule_at, name=f"workflow-showcase-{secrets.token_hex(6)}",
            repeat=1, skills=["workflow-showcase"],
        )
    evidence = {
        "schema_version": 1, "showcase_id": scenario.id, "schedule_id": job["id"],
        "nonce": secrets.token_hex(16), "definition_digest": scenario.package_digest,
        "expected_trigger": schedule_at, "profile": "default",
    }
    path = home / "workflow" / "showcase" / "schedules.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(evidence, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return {"status": "scheduled", "schedule_id": job["id"], "repeat": job["repeat"], "ownership": evidence}


def run_showcase(
    showcase_id: str, *, hermes_home: str | Path, symptom: str | None = None,
    confirmation_token: str | None = None, schedule_at: str | None = None,
    no_wait: bool = False, idempotency_key: str | None = None,
    trigger_source: str = "cli", source_instance: str | None = None,
    claimed_actor: str | None = None,
) -> dict[str, object]:
    scenario = load_showcase_catalog().get(showcase_id)
    if scenario is None:
        raise ShowcaseCatalogError(f"unknown showcase: {showcase_id}")
    if no_wait and not idempotency_key:
        raise ShowcaseCatalogError(
            "an idempotency key is required for no-wait showcase starts"
        )
    home = Path(hermes_home).expanduser().resolve()
    preflight = preflight_showcase(showcase_id, hermes_home=home)
    if scenario.interaction_mode == "schedule":
        if not schedule_at:
            return {"status": "skipped", "reason_code": "schedule_time_required", "run_id": None}
        return _schedule_showcase(scenario, home=home, schedule_at=schedule_at, token=confirmation_token)
    if showcase_id == "laptop-diagnostic" and not (symptom and symptom.strip()):
        return {"status": "input_required", "reason_code": "showcase_input_required", "required_input": "symptom", "run_id": None}
    fixture_dir: Path | None = None
    with _scenario_compilation(scenario) as compilation:
        package = compilation.package
        risk = _verified_distribution_risk(
            scenario,
            package,
            compilation=(
                compilation
                if supports_phase4_semantics(
                    package.language.effective_profile,
                    package.language.normalizer_version,
                )
                else None
            ),
        )
        store, config = _store(home, package)
        try:
            inputs = None
            if showcase_id == "laptop-diagnostic":
                fixture_dir, fixture = _stage_fixture(home)
                inputs = {"evidence": fixture}
            prepared = store.prepare_run_snapshot(
                package,
                compilation=(
                    compilation
                    if supports_phase4_semantics(
                        package.language.effective_profile,
                        package.language.normalizer_version,
                    )
                    else None
                ),
                inputs=inputs,
                values={"arguments": symptom or ""},
                execution_limits=RunExecutionLimits.resolve(config),
            )
        finally:
            if fixture_dir is not None:
                shutil.rmtree(fixture_dir, ignore_errors=True)
    intent_key = idempotency_key or secrets.token_urlsafe(24)
    request = RunAdmissionRequest(
        workflow_name=package.definition.name, definition_digest=prepared.definition_digest,
        policy_digest=prepared.policy_digest, input_manifest_digest=prepared.input_manifest_digest,
        trigger_source=trigger_source, idempotency_key=intent_key,
        idempotency_namespace=f"profile-local:{trigger_source}",
        concurrency_key=f"showcase:{scenario.id}",
        concurrency_policy=str(package.sidecar.get("overlap_policy") or "queue"),
        run_metadata=verified_showcase_run_metadata(
            showcase_id=scenario.id,
            showcase_version=scenario.package_version,
            bundle_digest=str(preflight["bundle_digest"]),
            risk_digest=risk.risk_digest,
            requires_ai=scenario.requires_ai,
            include_verified_marker=False,
        ),
        provenance=TriggerProvenance.local_admin_claim(
            source=trigger_source,
            intent_key=intent_key,
            source_instance=source_instance or f"cli:pid:{os.getpid()}",
            claimed_actor=claimed_actor,
        ),
    )
    admitted = store.start_run(request, immutable_snapshot=prepared)
    if admitted.run_id is None:
        return {"status": admitted.disposition, "reason_code": admitted.reason_code, "run_id": None}
    if no_wait or admitted.disposition != "created":
        return store.get_run_status(admitted.run_id)
    return _advance_until_wait(store, config, admitted.run_id)


def _current_showcase_interaction_id(store: RunStore, run_id: str) -> str:
    pending = store.get_run_status(run_id).get("pending_interaction")
    if not isinstance(pending, Mapping):
        raise ValueError("showcase run has no pending interaction")
    interaction_id = pending.get("interaction_id") or pending.get("action_digest")
    if not isinstance(interaction_id, str) or not interaction_id:
        raise ValueError("showcase pending interaction has no identity")
    return interaction_id


def approve_showcase(run_id: str, *, hermes_home: str | Path) -> dict[str, object]:
    store, config = _store(hermes_home)
    store.approve_run(
        run_id,
        interaction_id=_current_showcase_interaction_id(store, run_id),
        channel="showcase",
    )
    return _advance_until_wait(store, config, run_id)


def reject_showcase(run_id: str, reason: str, *, hermes_home: str | Path) -> dict[str, object]:
    store, config = _store(hermes_home)
    store.reject_run(
        run_id,
        reason=reason,
        interaction_id=_current_showcase_interaction_id(store, run_id),
        channel="showcase",
    )
    return _advance_until_wait(store, config, run_id)


def _event_ref(event: Mapping[str, object]) -> str:
    return f"event:{int(event['sequence'])}:{event['event_type']}"


def _report_events(
    store: RunStore, run_id: str, *, expected_sequence: int
) -> tuple[tuple[dict[str, object], ...], bool]:
    events: list[dict[str, object]] = []
    after = 0
    while after < expected_sequence and len(events) < 10_000:
        page = store.tail_events(run_id, after_sequence=after, limit=200)
        if not page:
            break
        events.extend(page)
        after = int(page[-1]["sequence"])
    complete = after == expected_sequence
    return tuple(events), complete


def _cleanup_evidence(
    projection: Mapping[str, object],
    events: tuple[dict[str, object], ...],
    *,
    journal_complete: bool,
) -> tuple[dict[str, object], tuple[str, ...]]:
    unresolved: dict[tuple[object, object, object], Mapping[str, object]] = {}
    failures: list[Mapping[str, object]] = []
    for event in events:
        event_type = event.get("event_type")
        payload = event.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        if event_type == "process_started":
            identity = payload.get("process_identity")
            if isinstance(identity, Mapping):
                key = (event.get("node_id"), event.get("attempt_id"), identity.get("pid"))
                unresolved[key] = event
        elif event_type == "process_reaped":
            key = (event.get("node_id"), event.get("attempt_id"), payload.get("pid"))
            unresolved.pop(key, None)
        elif event_type == "cleanup_failed":
            failures.append(event)
            pid = payload.get("pid")
            if pid is not None:
                key = (event.get("node_id"), event.get("attempt_id"), pid)
                unresolved[key] = event

    nodes = projection.get("nodes")
    if isinstance(nodes, Mapping):
        for node_id, node in nodes.items():
            if not isinstance(node, Mapping):
                continue
            for attempt in node.get("attempts", ()):
                if not isinstance(attempt, Mapping):
                    continue
                identity = attempt.get("process_identity")
                process_stop = attempt.get("process_stop")
                if isinstance(identity, Mapping) and not (
                    isinstance(process_stop, Mapping) and process_stop.get("cleaned")
                ):
                    key = (node_id, attempt.get("attempt_id"), identity.get("pid"))
                    unresolved[key] = attempt

    if failures and not unresolved:
        unresolved[("cleanup", "unproven", len(failures))] = failures[-1]
    if not journal_complete:
        unresolved[("journal", "incomplete", None)] = {}
    refs = tuple(_event_ref(event) for event in failures[-3:])
    return {
        "owned_processes_live": len(unresolved),
        "owned_processes_unreaped": len(unresolved),
        "cleanup_failures": len(failures),
        "journal_complete": journal_complete,
    }, refs


def _showcase_staging_present(home: Path) -> bool:
    staging = home / "workflow" / "showcase" / "staging"
    if not staging.is_dir():
        return False
    for child in staging.iterdir():
        marker = child / "owner.json"
        if not child.is_dir() or not marker.is_file():
            continue
        try:
            owner = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(owner, Mapping) and owner.get("owner") == "workflow-showcase":
            return True
    return False


def build_showcase_report(run_id: str, *, hermes_home: str | Path) -> ShowcaseReport:
    store, _ = _store(hermes_home)
    projection = store.load_run(run_id)
    metadata = projection.get("run_metadata", {})
    if not isinstance(metadata, Mapping) or not metadata.get("showcase_id"):
        raise ShowcaseCatalogError("run is not an authenticated showcase run")
    scenario = load_showcase_catalog()[str(metadata["showcase_id"])]
    events, journal_complete = _report_events(
        store, run_id, expected_sequence=int(projection["event_sequence"])
    )
    run_directory = store.run_directory(run_id)
    cleanup, cleanup_failure_refs = _cleanup_evidence(
        projection, events, journal_complete=journal_complete
    )
    cleanup["staging_present"] = _showcase_staging_present(
        Path(hermes_home).expanduser().resolve()
    )
    interactions = tuple(
        {"type": str(node["pending_interaction"].get("type", "interaction")), "node_id": node_id, "interaction_id": str(node["pending_interaction"].get("interaction_id", ""))}
        for node_id, node in projection["nodes"].items()
        if isinstance(node.get("pending_interaction"), Mapping)
    )
    verified_artifacts: list[dict] = []
    for index, artifact in enumerate(projection.get("artifacts", [])):
        relative = str(artifact.get("relative_path", ""))
        try:
            path = (run_directory / relative).resolve(strict=True)
            path.relative_to(run_directory.resolve())
            data = path.read_bytes()
            verified = _sha256(data) == artifact.get("sha256") and len(data) == artifact.get("size_bytes")
        except (OSError, ValueError):
            verified = False
        verified_artifacts.append({
            "name": Path(relative).name, "ref": f"artifact:{index}:{artifact.get('sha256', '')}",
            "verified": verified, "size_bytes": artifact.get("size_bytes", 0),
            "media_type": artifact.get("media_type", "application/octet-stream"),
        })
    by_type: dict[str, list[Mapping[str, object]]] = {}
    for event in events:
        by_type.setdefault(str(event.get("event_type")), []).append(event)
    names = {item["name"] for item in verified_artifacts if item["verified"]}
    claims: list[ShowcaseClaimResult] = []
    for capability in scenario.capability_claims:
        outcome: Literal["passed", "failed", "skipped"] = "failed"
        reason = "required_evidence_missing"
        refs: tuple[str, ...] = (f"scenario:{scenario.id}:catalog",)
        if capability == "immutable-inputs":
            manifest = run_directory / "inputs.json"
            if manifest.is_file() and "evidence" in json.loads(manifest.read_text()):
                outcome, reason, refs = "passed", "input_snapshot_verified", ("input:evidence:manifest",)
        elif capability == "parallel-fan-in":
            completed = [e for e in events if e.get("event_type") == "node_succeeded" and str(e.get("node_id", "")).startswith("analyze-")]
            render = [e for e in events if e.get("event_type") == "node_started" and e.get("node_id") == "render-report"]
            if len(completed) >= 2 and render:
                outcome, reason, refs = "passed", "fan_in_observed", tuple(_event_ref(e) for e in (*completed[:2], render[0]))
        elif capability == "approval-rework":
            rejections = by_type.get("interaction_rejected", [])
            approvals = by_type.get("interaction_approved", [])
            if rejections and approvals:
                outcome, reason, refs = "passed", "rework_and_approval_observed", tuple(_event_ref(e) for e in (rejections[-1], approvals[-1]))
            elif projection["status"] == "paused":
                outcome, reason = "skipped", "awaiting_operator_decision"
        elif capability == "operator-approval":
            approvals = by_type.get("interaction_approved", [])
            if approvals:
                outcome, reason, refs = (
                    "passed",
                    "operator_approval_observed",
                    (_event_ref(approvals[-1]),),
                )
            elif projection["status"] == "paused":
                outcome, reason = "skipped", "awaiting_operator_decision"
        elif capability == "artifact-verification":
            if set(scenario.expected_artifacts) <= names:
                outcome, reason, refs = "passed", "expected_artifacts_verified", tuple(str(a["ref"]) for a in verified_artifacts if a["name"] in scenario.expected_artifacts)
        elif capability == "no-live-inventory":
            outcome, reason, refs = "passed", "sanitized_fixture_only", ("input:evidence:fictional",)
        elif capability == "persisted-retry":
            attempts = projection["nodes"].get("retry", {}).get("attempts", [])
            if len(attempts) == 2 and projection["nodes"]["retry"]["state"] == "succeeded":
                outcome, reason, refs = "passed", "fail_once_then_succeed", tuple(f"attempt:retry:{i + 1}" for i in range(2))
            elif projection["nodes"].get("retry", {}).get("state") == "skipped":
                outcome, reason = "skipped", "mode_not_selected"
        elif capability == "typed-timeout":
            node = projection["nodes"].get("timeout", {})
            attempts = node.get("attempts", [])
            error_code = attempts[-1].get("error_code") if attempts else None
            if error_code == "timeout":
                reaped = by_type.get("process_reaped", [])
                outcome, reason, refs = "passed", "timeout_and_reap_observed", tuple(_event_ref(e) for e in reaped[-1:]) or ("node:timeout:last_error",)
            elif node.get("state") == "skipped":
                outcome, reason = "skipped", "mode_not_selected"
        elif capability == "process-cleanup":
            reaped = by_type.get("process_reaped", [])
            if cleanup["owned_processes_unreaped"] == 0 and not cleanup["cleanup_failures"]:
                outcome, reason, refs = "passed", "all_owned_processes_reaped", tuple(_event_ref(e) for e in reaped[-3:]) or ("run:process:none",)
            else:
                reason = "owned_process_cleanup_unproven"
                refs = cleanup_failure_refs or ("run:process:unreaped",)
        elif scenario.requires_ai:
            outcome, reason = "skipped", "optional_ai_evidence_not_available"
        elif scenario.id == "scheduling":
            outcome, reason = "skipped", "schedule_evidence_is_external_to_run"
        claims.append(ShowcaseClaimResult(capability, outcome, reason, refs))
    return ShowcaseReport(
        schema_version=1, showcase_id=scenario.id, showcase_version=str(metadata["showcase_version"]),
        bundle_digest=str(metadata["bundle_digest"]), run_id=run_id,
        definition_digest=str(projection["definition_digest"]), terminal_outcome=str(projection["status"]),
        claims=tuple(claims), interactions=interactions, artifacts=tuple(verified_artifacts),
        cleanup=cleanup,
        suggested_next=("approve", "reject") if projection["status"] == "paused" else ("status", "cleanup"),
    )


def reset_showcase(showcase_id: str, *, hermes_home: str | Path) -> dict[str, object]:
    home = Path(hermes_home).resolve()
    staging = home / "workflow" / "showcase" / "staging"
    if staging.is_dir():
        for child in staging.iterdir():
            marker = child / "owner.json"
            if child.is_dir() and marker.is_file():
                shutil.rmtree(child, ignore_errors=True)
    ownership_path = home / "workflow" / "showcase" / "schedules.json"
    owned_schedule = None
    if showcase_id == "scheduling" and ownership_path.is_file():
        evidence = json.loads(ownership_path.read_text(encoding="utf-8"))
        with use_cron_store(home):
            owned_schedule = next((job for job in list_jobs() if job.get("id") == evidence.get("schedule_id")), None)
    return {"showcase_id": showcase_id, "owned_schedule": owned_schedule, "removed": False, "requires_explicit_cron_removal": owned_schedule is not None}


def cleanup_showcases(
    *,
    hermes_home: str | Path,
    execute: bool = False,
    confirmation_token: str | None = None,
    older_than_days: int = 7,
) -> dict[str, object]:
    store, _ = _store(hermes_home)
    tagged = [run["run_id"] for run in store.list_runs(limit=200) if isinstance(run.get("run_metadata"), Mapping) and run["run_metadata"].get("showcase_id")]
    result = store.cleanup_runs(
        older_than=timedelta(days=older_than_days),
        execute=execute,
        confirmation_token=confirmation_token,
        required_metadata={"showcase_id": None},
    )
    return {**result, "showcase_run_ids": tagged}


def report_to_dict(report: ShowcaseReport) -> dict[str, object]:
    return asdict(report)


__all__ = [
    "ShowcaseCatalogError", "ShowcaseClaimResult", "ShowcaseReport", "ShowcaseScenario",
    "VerifiedShowcasePackage",
    "approve_showcase", "build_showcase_report", "cleanup_showcases", "load_showcase_catalog",
    "load_verified_showcase_package", "load_verified_showcase_packages",
    "preflight_showcase", "reject_showcase", "report_to_dict", "reset_showcase", "run_showcase",
    "showcase_background_api_eligible",
]
