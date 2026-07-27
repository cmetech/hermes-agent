"""Read-only fire-time authorization for scheduled workflow runs."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Iterable, Mapping

from plugins.workflow.catalog_api import (
    CATALOG_MAX_TRUST_STORE_BYTES,
    resolve_workflow_catalog_package,
)
from plugins.workflow.language import (
    WorkflowLanguageCompatibilityError,
    read_language_snapshot,
)
from plugins.workflow.models import WorkflowLanguageProfile
from plugins.workflow.runner_binding import (
    ExecutionCapabilityContext,
    WorkflowRunnerBinding,
    assess_package_execution,
)
from plugins.workflow.trust import (
    WORKFLOW_RESOURCE_MAX_FILE_BYTES,
    WORKFLOW_RESOURCE_MAX_FILES,
    WORKFLOW_RESOURCE_MAX_TOTAL_BYTES,
    WorkflowResourceCapacityError,
    WorkflowResourceReadBudget,
    WorkflowTrustStore,
    compute_package_digest,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RESOURCE_FILE_BYTES = WORKFLOW_RESOURCE_MAX_FILE_BYTES
_RESOURCE_TOTAL_BYTES = WORKFLOW_RESOURCE_MAX_TOTAL_BYTES
_RESOURCE_FILES = WORKFLOW_RESOURCE_MAX_FILES
_SOURCE_IDENTITY_CHARS = 512
_SEALED_SNAPSHOT_FILES = 4096
_SEALED_SNAPSHOT_PATH_CHARS = 512
_SEALED_SNAPSHOT_REQUIRED = frozenset(
    {"definition.yaml", "inputs.json", "resources.json"}
)
_MUTABLE_RUN_FILES = frozenset({
    ".lock",
    ".snapshot-owner.json",
    "events.jsonl",
    "run.json",
})
# These are the only namespaces written by node executors after admission.
# They remain non-authoritative: resource resolution is separately restricted
# to journal-corroborated sealed paths.
_MUTABLE_RUN_ROOTS = frozenset({"artifacts", "nodes"})


class ScheduledRunRevalidationError(RuntimeError):
    """The current source or execution authority no longer matches admission."""


def showcase_scenario_digest(scenario: object) -> str:
    """Return a stable digest for the exact authenticated scenario record."""
    material = json.dumps(
        asdict(scenario),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def read_sealed_snapshot_paths(value: object) -> tuple[str, ...]:
    """Parse the bounded, canonical file identities sealed at admission."""
    if not isinstance(value, (list, tuple)) or not value:
        raise ScheduledRunRevalidationError("sealed snapshot paths are missing")
    if len(value) > _SEALED_SNAPSHOT_FILES:
        raise ScheduledRunRevalidationError("sealed snapshot has too many paths")
    paths: list[str] = []
    seen: set[str] = set()
    for item in value:
        if (
            not isinstance(item, str)
            or not item
            or len(item) > _SEALED_SNAPSHOT_PATH_CHARS
            or "\\" in item
            or "\0" in item
        ):
            raise ScheduledRunRevalidationError("sealed snapshot path is invalid")
        relative = PurePosixPath(item)
        if (
            relative.is_absolute()
            or relative.as_posix() != item
            or any(part in {"", ".", ".."} for part in relative.parts)
            or item in _MUTABLE_RUN_FILES
            or item in seen
        ):
            raise ScheduledRunRevalidationError("sealed snapshot path is invalid")
        seen.add(item)
        paths.append(item)
    if paths != sorted(paths):
        raise ScheduledRunRevalidationError("sealed snapshot paths are not canonical")
    if not _SEALED_SNAPSHOT_REQUIRED.issubset(seen):
        raise ScheduledRunRevalidationError("sealed snapshot paths are incomplete")
    return tuple(paths)


def sealed_snapshot_digest(
    root: str | Path,
    *,
    relative_paths: Iterable[str] | None = None,
    read_budget: WorkflowResourceReadBudget | None = None,
    allow_unsealed_regular_files: bool = False,
) -> str:
    """Digest the immutable files sealed at admission, or a legacy run tree."""
    snapshot_root = Path(root)
    entries: list[tuple[str, Path]] = []
    if relative_paths is not None:
        sealed_paths = read_sealed_snapshot_paths(tuple(relative_paths))
        sealed_set = frozenset(sealed_paths)
        sealed_directories = {
            parent.as_posix()
            for relative in sealed_paths
            for parent in PurePosixPath(relative).parents
            if parent.as_posix() != "."
        }
        pending = [snapshot_root]
        while pending:
            directory = pending.pop()
            try:
                children = tuple(os.scandir(directory))
            except OSError as exc:
                raise ScheduledRunRevalidationError(
                    "sealed snapshot is unreadable"
                ) from exc
            for entry in children:
                path = Path(entry.path)
                relative = path.relative_to(snapshot_root).as_posix()
                first_part = PurePosixPath(relative).parts[0]
                try:
                    if entry.is_symlink():
                        raise ScheduledRunRevalidationError(
                            "sealed snapshot contains a symlink"
                        )
                    if entry.is_dir(follow_symlinks=False):
                        if (
                            not allow_unsealed_regular_files
                            and first_part not in _MUTABLE_RUN_ROOTS
                            and relative not in sealed_directories
                        ):
                            raise ScheduledRunRevalidationError(
                                "sealed snapshot contains an unsealed path"
                            )
                        pending.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        if relative in sealed_set:
                            entries.append((relative, path))
                        elif (
                            not allow_unsealed_regular_files
                            and relative not in _MUTABLE_RUN_FILES
                            and first_part not in _MUTABLE_RUN_ROOTS
                        ):
                            raise ScheduledRunRevalidationError(
                                "sealed snapshot contains an unsealed path"
                            )
                    else:
                        raise ScheduledRunRevalidationError(
                            "sealed snapshot contains a special file"
                        )
                except OSError as exc:
                    raise ScheduledRunRevalidationError(
                        "sealed snapshot is unreadable"
                    ) from exc
        if {relative for relative, _path in entries} != sealed_set:
            raise ScheduledRunRevalidationError("sealed snapshot path is missing")
    else:
        pending = [snapshot_root]
        while pending:
            directory = pending.pop()
            try:
                children = tuple(os.scandir(directory))
            except OSError as exc:
                raise ScheduledRunRevalidationError(
                    "sealed snapshot is unreadable"
                ) from exc
            for entry in children:
                path = Path(entry.path)
                relative = path.relative_to(snapshot_root).as_posix()
                first_part = PurePosixPath(relative).parts[0]
                try:
                    if entry.is_symlink():
                        raise ScheduledRunRevalidationError(
                            "sealed snapshot contains a symlink"
                        )
                    if entry.is_dir(follow_symlinks=False):
                        if first_part in _MUTABLE_RUN_ROOTS:
                            continue
                        pending.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        if relative not in _MUTABLE_RUN_FILES:
                            entries.append((relative, path))
                    else:
                        raise ScheduledRunRevalidationError(
                            "sealed snapshot contains a special file"
                        )
                except OSError as exc:
                    raise ScheduledRunRevalidationError(
                        "sealed snapshot is unreadable"
                    ) from exc

    digest = hashlib.sha256()
    for relative, path in sorted(entries):
        try:
            before = path.stat()
            data = (
                read_budget.read(path, verify_cached_identity=True)
                if read_budget is not None
                else path.read_bytes()
            )
            after = path.stat()
        except OSError as exc:
            raise ScheduledRunRevalidationError(
                "sealed snapshot is unreadable"
            ) from exc
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or len(data) != before.st_size:
            raise ScheduledRunRevalidationError("sealed snapshot changed during read")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def scheduled_catalog_source_identity(
    package,
    *,
    hermes_home: str | Path,
    workdir: str | Path,
) -> tuple[str, str]:
    """Return a bounded server-derived root and relative exact catalog path."""
    if package.source == "profile":
        source_root = (Path(hermes_home).resolve() / "workflows").resolve(strict=True)
    elif package.source == "project":
        source_root = (Path(workdir).resolve() / ".hermes" / "workflows").resolve(
            strict=True
        )
    else:
        raise ScheduledRunRevalidationError("catalog source is invalid")
    workflow_path = package.workflow_path.resolve(strict=True)
    try:
        relative = workflow_path.relative_to(source_root).as_posix()
    except ValueError as exc:
        raise ScheduledRunRevalidationError(
            "catalog workflow escapes its source root"
        ) from exc
    root_text = str(source_root)
    if (
        not relative
        or len(root_text) > _SOURCE_IDENTITY_CHARS
        or len(relative) > _SOURCE_IDENTITY_CHARS
    ):
        raise ScheduledRunRevalidationError("catalog source identity is too long")
    return root_text, relative


def scheduled_execution_context(
    run: Mapping[str, object],
    binding: WorkflowRunnerBinding,
) -> ExecutionCapabilityContext:
    """Derive actual fire-time capability from server-owned run evidence."""
    metadata = run.get("run_metadata")
    if not isinstance(metadata, Mapping):
        raise ScheduledRunRevalidationError("scheduled admission metadata is missing")
    is_showcase = metadata.get("catalog_source") == "showcase"
    if is_showcase:
        entitlement_value = (
            "real" if metadata.get("ai_entitlement") == "real" else "deterministic"
        )
    else:
        entitlement_value = "real"
    from plugins.workflow.entitlement import AIEntitlementResolution

    return binding.execution_context(
        surface="background",
        entitlement=AIEntitlementResolution(entitlement_value),
    )


def _required_digest(metadata: Mapping[str, object], name: str) -> str:
    value = metadata.get(name)
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ScheduledRunRevalidationError(f"scheduled {name} is missing")
    return value


def verify_sealed_snapshot(
    run: Mapping[str, object],
    *,
    run_directory: Path,
    read_budget: WorkflowResourceReadBudget | None = None,
) -> None:
    definition = run_directory / "definition.yaml"
    policy = run_directory / "policy.yaml"
    resources = run_directory / "resources.json"
    budget = read_budget or WorkflowResourceReadBudget(
        max_file_bytes=_RESOURCE_FILE_BYTES,
        max_total_bytes=_RESOURCE_TOTAL_BYTES,
        max_files=_RESOURCE_FILES,
    )
    try:
        definition_bytes = budget.read(definition, verify_cached_identity=True)
        policy_bytes = (
            budget.read(policy, verify_cached_identity=True)
            if policy.is_file() or policy.is_symlink()
            else b"{}\n"
        )
        resources_bytes = budget.read(resources, verify_cached_identity=True)
    except (OSError, WorkflowResourceCapacityError) as exc:
        raise ScheduledRunRevalidationError("sealed snapshot is unreadable") from exc
    expected = (
        _required_digest(run["run_metadata"], "sealed_definition_digest"),
        _required_digest(run["run_metadata"], "sealed_policy_digest"),
        _required_digest(run["run_metadata"], "sealed_input_digest"),
    )
    actual = (
        hashlib.sha256(definition_bytes).hexdigest(),
        hashlib.sha256(policy_bytes).hexdigest(),
        hashlib.sha256(resources_bytes).hexdigest(),
    )
    if expected != actual:
        raise ScheduledRunRevalidationError("sealed snapshot identity changed")
    if expected[1:] != (
        str(run.get("policy_digest") or ""),
        str(run.get("input_manifest_digest") or ""),
    ):
        raise ScheduledRunRevalidationError("sealed admission identity changed")
    expected_snapshot_digest = _required_digest(
        run["run_metadata"], "sealed_snapshot_digest"
    )
    projected_snapshot_digest = run.get("sealed_snapshot_digest")
    if projected_snapshot_digest is not None and (
        not isinstance(projected_snapshot_digest, str)
        or _SHA256.fullmatch(projected_snapshot_digest) is None
        or projected_snapshot_digest != expected_snapshot_digest
    ):
        raise ScheduledRunRevalidationError("sealed snapshot identity changed")
    if run.get("language") is not None and projected_snapshot_digest is None:
        raise ScheduledRunRevalidationError("sealed snapshot identity changed")
    try:
        resources_document = json.loads(resources_bytes)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ScheduledRunRevalidationError("sealed snapshot is unreadable") from exc
    if not isinstance(resources_document, Mapping):
        raise ScheduledRunRevalidationError("sealed snapshot is unreadable")
    try:
        projected_language = read_language_snapshot(run.get("language"))
        sealed_language = read_language_snapshot(resources_document.get("language"))
    except WorkflowLanguageCompatibilityError as exc:
        raise ScheduledRunRevalidationError(
            "sealed snapshot language identity changed"
        ) from exc
    if projected_language != sealed_language:
        raise ScheduledRunRevalidationError(
            "sealed snapshot language identity changed"
        )
    sealed_paths = (
        read_sealed_snapshot_paths(resources_document.get("sealed_paths"))
        if projected_snapshot_digest is not None
        else None
    )
    if (
        sealed_snapshot_digest(
            run_directory,
            relative_paths=sealed_paths,
            read_budget=budget,
            allow_unsealed_regular_files=(
                sealed_language is not None
                and sealed_language.effective_profile
                is WorkflowLanguageProfile.HERMES_LEGACY
            ),
        )
        != expected_snapshot_digest
    ):
        raise ScheduledRunRevalidationError("sealed snapshot identity changed")


def _load_exact_catalog_package(
    run: Mapping[str, object],
    *,
    hermes_home: Path,
):
    metadata = run["run_metadata"]
    assert isinstance(metadata, Mapping)
    source = metadata.get("catalog_source")
    root_text = metadata.get("catalog_source_root")
    relative_text = metadata.get("catalog_source_relative")
    if (
        source not in {"project", "profile"}
        or not isinstance(root_text, str)
        or not root_text
        or len(root_text) > _SOURCE_IDENTITY_CHARS
        or not isinstance(relative_text, str)
        or not relative_text
        or len(relative_text) > _SOURCE_IDENTITY_CHARS
    ):
        raise ScheduledRunRevalidationError("exact catalog source is missing")
    relative = Path(relative_text)
    if relative.is_absolute() or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise ScheduledRunRevalidationError("exact catalog source is invalid")
    try:
        root = Path(root_text)
        if not root.is_absolute() or root.resolve(strict=True) != root:
            raise ScheduledRunRevalidationError("exact catalog root changed")
        if source == "profile" and root != (hermes_home / "workflows").resolve(
            strict=True
        ):
            raise ScheduledRunRevalidationError("profile catalog root changed")
        workflow_path = (root / relative).resolve(strict=True)
        workflow_path.relative_to(root)
        if source == "project":
            if root.name != "workflows" or root.parent.name != ".hermes":
                raise ScheduledRunRevalidationError("project catalog root is invalid")
            exact_workdir = root.parent.parent
        else:
            exact_workdir = root
        package = resolve_workflow_catalog_package(
            str(run.get("workflow") or ""),
            hermes_home=hermes_home,
            workdir=exact_workdir,
            catalog_source=source,
        )
    except ScheduledRunRevalidationError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise ScheduledRunRevalidationError("catalog source is unavailable") from exc
    if (
        package is None
        or package.definition.name != str(run.get("workflow") or "")
        or package.workflow_path.resolve(strict=True) != workflow_path
    ):
        raise ScheduledRunRevalidationError("catalog workflow identity changed")
    return package


def revalidate_scheduled_run(
    run: Mapping[str, object],
    execution_capability_context: ExecutionCapabilityContext,
    *,
    hermes_home: str | Path,
    run_directory: str | Path,
    read_budget: WorkflowResourceReadBudget | None = None,
) -> None:
    """Reauthorize one scheduled run without mutating its durable state."""
    metadata = run.get("run_metadata")
    if not isinstance(metadata, Mapping) or not isinstance(
        metadata.get("schedule_at"), str
    ):
        raise ScheduledRunRevalidationError("scheduled admission metadata is missing")
    run_id = run.get("run_id")
    state_version = run.get("state_version")
    if not isinstance(run_id, str) or not isinstance(state_version, int):
        raise ScheduledRunRevalidationError("scheduled run identity is missing")
    execution_identity = _required_digest(metadata, "execution_identity")
    if execution_identity != execution_capability_context.identity_digest:
        raise ScheduledRunRevalidationError("execution capability changed")
    package_identity = _required_digest(metadata, "package_digest")
    risk_identity = _required_digest(metadata, "risk_digest")
    if package_identity != str(run.get("definition_digest")):
        raise ScheduledRunRevalidationError("admission package identity changed")
    budget = read_budget or WorkflowResourceReadBudget(
        max_file_bytes=_RESOURCE_FILE_BYTES,
        max_total_bytes=_RESOURCE_TOTAL_BYTES,
        max_files=_RESOURCE_FILES,
    )
    verify_sealed_snapshot(
        run,
        run_directory=Path(run_directory),
        read_budget=budget,
    )
    source = metadata.get("catalog_source")
    if source == "showcase":
        from plugins.workflow.showcase import load_verified_showcase_package

        showcase_id = metadata.get("showcase_id")
        if not isinstance(showcase_id, str):
            raise ScheduledRunRevalidationError("showcase identity is missing")
        try:
            verified = load_verified_showcase_package(
                showcase_id,
                read_budget=budget,
                force_reverify=True,
            )
            compatibility, risk = assess_package_execution(
                verified.package,
                execution_capability_context,
                read_budget=budget,
            )
        except Exception as exc:
            raise ScheduledRunRevalidationError("showcase verification failed") from exc
        comparisons = (
            (metadata.get("showcase_version"), verified.scenario.package_version),
            (metadata.get("bundle_digest"), verified.bundle_digest),
            (
                metadata.get("showcase_scenario_digest"),
                showcase_scenario_digest(verified.scenario),
            ),
            (package_identity, verified.package_digest),
            (risk_identity, risk.risk_digest),
        )
        if not compatibility.runnable or not all(
            isinstance(left, str) and left == str(right) for left, right in comparisons
        ):
            raise ScheduledRunRevalidationError("showcase identity changed")
    elif source in {"project", "profile"}:
        try:
            package = _load_exact_catalog_package(
                run,
                hermes_home=Path(hermes_home),
            )
            live_digest = compute_package_digest(package, read_budget=budget).sha256
            compatibility, risk = assess_package_execution(
                package,
                execution_capability_context,
                read_budget=budget,
            )
            trust = WorkflowTrustStore(Path(hermes_home)).snapshot_read_only(
                max_bytes=CATALOG_MAX_TRUST_STORE_BYTES
            )
        except ScheduledRunRevalidationError:
            raise
        except Exception as exc:
            raise ScheduledRunRevalidationError("catalog verification failed") from exc
        if (
            not compatibility.runnable
            or package_identity != live_digest
            or risk_identity != risk.risk_digest
            or WorkflowTrustStore(Path(hermes_home)).check_snapshot(
                trust,
                live_digest,
                risk_digest=risk.risk_digest,
            )
            != "trusted"
        ):
            raise ScheduledRunRevalidationError("catalog authorization changed")
    else:
        raise ScheduledRunRevalidationError("catalog source is missing")


__all__ = [
    "ScheduledRunRevalidationError",
    "read_sealed_snapshot_paths",
    "revalidate_scheduled_run",
    "scheduled_catalog_source_identity",
    "scheduled_execution_context",
    "sealed_snapshot_digest",
    "showcase_scenario_digest",
    "verify_sealed_snapshot",
]
