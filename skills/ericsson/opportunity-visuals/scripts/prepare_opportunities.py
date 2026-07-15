#!/usr/bin/env python3
"""Inspect and prepare deterministic opportunity visual inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from opportunity_data import (
    DataContractError,
    apply_filters,
    inspect_source,
    load_source,
    normalize_rows,
    resolve_mapping,
    row_id,
    select_records,
    strict_json_loads,
    validate_semantics,
)


def _load_json_object(path: Path, kind: str) -> dict[str, object]:
    try:
        value = strict_json_loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DataContractError(
            f"invalid_{kind}", f"Unable to read {kind} JSON: {path.name}"
        ) from error
    if not isinstance(value, dict):
        raise DataContractError(f"invalid_{kind}", f"{kind.capitalize()} must be an object")
    return value


def _selected_mapping(
    mapping: dict[str, object], requested_months: list[str] | None
) -> tuple[dict[str, object], list[dict[str, str]], int]:
    available = list(mapping["months"])
    if not requested_months:
        selected = available
        first_index = 0
    else:
        requested = {month.casefold() for month in requested_months}
        selected = [
            month
            for month in available
            if str(month["label"]).casefold() in requested
            or str(month["key"]).casefold() in requested
        ]
        matched = {
            requested_value
            for requested_value in requested
            if any(
                requested_value
                in {str(month["label"]).casefold(), str(month["key"]).casefold()}
                for month in available
            )
        }
        missing = sorted(requested - matched)
        if missing:
            raise DataContractError(
                "month_not_found", "Requested month was not found", {"months": missing}
            )
        first_index = available.index(selected[0])
    if not selected:
        raise DataContractError("missing_months", "At least one month must be selected")
    selected_mapping = {**mapping, "months": selected}
    selected_months = [
        {"key": str(month["key"]), "label": str(month["label"])}
        for month in selected
    ]
    return selected_mapping, selected_months, first_index


def _terminal_before_range_exclusions(
    rows: list[dict[str, object]],
    all_months: list[dict[str, object]],
    first_selected_index: int,
    semantics: dict[str, object],
) -> list[dict[str, object]]:
    positive = {str(stage).casefold() for stage in semantics["positive_terminals"]}
    negative = {str(stage).casefold() for stage in semantics["negative_terminals"]}
    excluded: list[dict[str, object]] = []
    for source_index, row in enumerate(rows):
        terminal = next(
            (
                str(row.get(str(month["stage"]), ""))
                for month in all_months[:first_selected_index]
                if str(row.get(str(month["stage"]), "")).casefold()
                in positive | negative
            ),
            None,
        )
        if terminal is not None:
            source_row = source_index + 2
            excluded.append(
                {
                    "id": row_id(row, source_row),
                    "source_row": source_row,
                    "code": "terminal_before_range",
                    "message": "Row reached a terminal before the selected range",
                }
            )
    return excluded


@dataclass(frozen=True)
class _OwnedPublication:
    path: Path
    owner_handle: BinaryIO
    owner_stat: os.stat_result
    stage_path: Path
    expected_sha256: str


def _sha256_handle(handle: BinaryIO) -> str:
    handle.flush()
    position = handle.tell()
    handle.seek(0)
    digest = hashlib.sha256()
    while chunk := handle.read(1024 * 1024):
        digest.update(chunk)
    handle.seek(position)
    return digest.hexdigest()


def _remove_owned_name(path: Path, owner_stat: os.stat_result) -> None:
    """Remove only a name for owner_stat, restoring captured competitors safely."""

    quarantine_dir: Path | None = None
    candidate: Path | None = None
    try:
        try:
            quarantine_dir = Path(
                tempfile.mkdtemp(
                    prefix=f".{path.name}.rollback-",
                    dir=path.parent,
                )
            )
            candidate = quarantine_dir / "candidate"
            os.rename(path, candidate)
        except (FileNotFoundError, OSError):
            return

        candidate_stat = candidate.lstat()
        if os.path.samestat(owner_stat, candidate_stat):
            candidate.unlink()
            return

        try:
            os.link(candidate, path, follow_symlinks=False)
        except FileExistsError:
            return
        try:
            restored_stat = path.lstat()
        except FileNotFoundError:
            return
        if not os.path.samestat(candidate_stat, restored_stat):
            return
        candidate.unlink()
    finally:
        if quarantine_dir is not None:
            try:
                quarantine_dir.rmdir()
            except OSError:
                pass


def _atomic_json(path: Path, value: object) -> _OwnedPublication:
    """Publish JSON while retaining the original open file as owner evidence."""

    path = Path(path)
    temporary = path.with_name(f".{path.name}.tmp")
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if path.exists() or path.is_symlink() or temporary.exists() or temporary.is_symlink():
        raise FileExistsError("Output artifact already exists")
    handle: BinaryIO | None = None
    try:
        handle = temporary.open("x+b")
        handle.write(encoded)
        handle.flush()
        owner_stat = os.fstat(handle.fileno())
        if not stat.S_ISREG(owner_stat.st_mode):
            raise OSError("Staging artifact is not a regular file")
        expected_sha256 = hashlib.sha256(encoded).hexdigest()
        if _sha256_handle(handle) != expected_sha256:
            raise OSError("Staging artifact changed before publication")
        try:
            staged_stat = temporary.lstat()
        except OSError:
            raise OSError("Staging artifact changed before publication") from None
        if not os.path.samestat(owner_stat, staged_stat):
            raise OSError("Staging artifact changed before publication")
        os.link(temporary, path, follow_symlinks=False)
        try:
            visible_stat = path.lstat()
            retained_stat = os.fstat(handle.fileno())
        except OSError:
            raise OSError("Output artifact changed during publication") from None
        if (
            not stat.S_ISREG(retained_stat.st_mode)
            or not os.path.samestat(owner_stat, retained_stat)
            or not os.path.samestat(retained_stat, visible_stat)
            or _sha256_handle(handle) != expected_sha256
        ):
            raise OSError("Output artifact changed during publication")
        _remove_owned_name(temporary, retained_stat)
    except BaseException:
        if handle is not None:
            try:
                owner_stat = os.fstat(handle.fileno())
            except OSError:
                owner_stat = None
            if owner_stat is not None:
                _remove_owned_name(path, owner_stat)
                _remove_owned_name(temporary, owner_stat)
            handle.close()
        raise
    assert handle is not None
    return _OwnedPublication(
        path=path,
        owner_handle=handle,
        owner_stat=owner_stat,
        stage_path=temporary,
        expected_sha256=expected_sha256,
    )


def _release_owner(publication: _OwnedPublication) -> None:
    publication.owner_handle.close()
    _remove_owned_name(publication.stage_path, publication.owner_stat)


def _verify_publications(publications: list[_OwnedPublication]) -> None:
    for publication in publications:
        try:
            owner_stat = os.fstat(publication.owner_handle.fileno())
            visible_stat = publication.path.lstat()
            actual_sha256 = _sha256_handle(publication.owner_handle)
        except OSError:
            raise DataContractError(
                "output_changed", "An output artifact changed before commit"
            ) from None
        if (
            not stat.S_ISREG(owner_stat.st_mode)
            or not os.path.samestat(owner_stat, visible_stat)
            or actual_sha256 != publication.expected_sha256
        ):
            raise DataContractError(
                "output_changed", "An output artifact changed before commit"
            )


def _rollback_owned(publication: _OwnedPublication) -> None:
    """Remove only this transaction's inode and preserve a competing replacement."""

    try:
        try:
            owner_stat = os.fstat(publication.owner_handle.fileno())
        except OSError:
            return
        _remove_owned_name(publication.path, owner_stat)
    finally:
        _release_owner(publication)


def _validate_output_destination(output_dir: Path) -> bool:
    """Return whether the destination must be created, rejecting unsafe reuse."""

    try:
        if output_dir.is_symlink():
            raise DataContractError(
                "output_exists", "Output destination already exists"
            )
        if output_dir.exists():
            if not output_dir.is_dir() or any(output_dir.iterdir()):
                raise DataContractError(
                    "output_exists", "Output destination already exists"
                )
            return False
    except DataContractError:
        raise
    except OSError:
        raise DataContractError(
            "output_exists", "Output destination already exists"
        ) from None
    return True


def _write_artifacts(
    output_dir: Path,
    artifacts: list[tuple[str, object]],
    create_output_dir: bool,
) -> None:
    publications: list[_OwnedPublication] = []
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, value in artifacts:
            path = output_dir / name
            publications.append(_atomic_json(path, value))
        _verify_publications(publications)
    except BaseException as error:
        for publication in reversed(publications):
            _rollback_owned(publication)
        if create_output_dir:
            try:
                output_dir.rmdir()
            except OSError:
                pass
        if not isinstance(error, Exception):
            raise
        if isinstance(error, DataContractError):
            raise
        raise DataContractError(
            "output_unwritable", "Unable to write output artifacts"
        ) from None
    for publication in publications:
        _release_owner(publication)


def _selected_formula_errors(
    metadata: dict[str, object],
    mapping: dict[str, object],
    selected_mapping: dict[str, object],
    excluded_source_rows: set[int] | None = None,
) -> list[dict[str, object]]:
    fixed_headers = {
        str(mapping[field])
        for field in ("area", "sub_area", "opportunity_name", "tcv", "probability")
    }
    selected_headers: set[str] = set()
    for month in selected_mapping["months"]:
        selected_headers.add(str(month["stage"]))
        if "probability" in month:
            selected_headers.add(str(month["probability"]))
    excluded_source_rows = excluded_source_rows or set()
    return [
        item
        for item in metadata.get("uncached_formulas", [])
        if int(item["row"]) not in excluded_source_rows
        and (
            item["header"] in fixed_headers
            or item["header"] in selected_headers
        )
    ]


def _scan_pre_range_stages(
    rows: list[dict[str, object]],
    pre_range_months: list[dict[str, object]],
    semantics: dict[str, object],
    metadata: dict[str, object],
) -> tuple[list[str], set[int], list[dict[str, object]]]:
    positive = {str(stage).casefold() for stage in semantics["positive_terminals"]}
    negative = {str(stage).casefold() for stage in semantics["negative_terminals"]}
    formula_cells = {
        (int(item["row"]), str(item["header"])): item
        for item in metadata.get("uncached_formulas", [])
    }
    observed_stages: list[str] = []
    terminal_source_rows: set[int] = set()
    formula_errors: list[dict[str, object]] = []
    for source_row, row in enumerate(rows, start=2):
        control_only = False
        for month in pre_range_months:
            header = str(month["stage"])
            formula_error = formula_cells.get((source_row, header))
            if formula_error is not None:
                if not control_only:
                    formula_errors.append(formula_error)
                control_only = True
                continue
            stage = str(row.get(header, ""))
            if not stage.strip():
                continue
            if stage.casefold() in positive | negative:
                terminal_source_rows.add(source_row)
                break
            if not control_only:
                observed_stages.append(stage)
    return observed_stages, terminal_source_rows, formula_errors


def _group_transitions(
    records: list[dict[str, object]],
    view: str,
    classification: str,
    code: str,
    semantics: dict[str, object],
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for record in records:
        has_positive = any(
            month["classification"] in {"positive", "won"}
            for month in record["months"]
        )
        previous_stage: str | None = None
        for month in record["months"]:
            stage = str(month["stage"])
            if not stage.strip():
                continue
            if previous_stage is not None and month["classification"] == classification:
                key = (previous_stage, stage)
                terminal_status_resolved = _terminal_status_resolved(stage, semantics)
                entry = grouped.setdefault(
                    key,
                    {
                        "from_stage": previous_stage,
                        "to_stage": stage,
                        "code": code,
                        "occurrences": 0,
                        "affects_inclusion": False,
                        "terminal_status_resolved": terminal_status_resolved,
                        "affects_truncation": not terminal_status_resolved,
                    },
                )
                entry["occurrences"] = int(entry["occurrences"]) + 1
                if not terminal_status_resolved or (
                    classification == "unknown"
                    and view == "positive-progression"
                    and not has_positive
                ):
                    entry["affects_inclusion"] = True
            previous_stage = stage
    return [
        grouped[key]
        for key in sorted(
            grouped,
            key=lambda pair: (
                pair[0].casefold(),
                pair[0],
                pair[1].casefold(),
                pair[1],
            ),
        )
    ]


def _known_terminal_status_stages(semantics: dict[str, object]) -> set[str]:
    known = {
        str(stage).casefold()
        for key in (
            "positive_terminals",
            "negative_terminals",
            "non_terminal_stages",
        )
        for stage in semantics[key]
    }
    for path in semantics["stage_paths"]:
        known.update(str(stage).casefold() for stage in path)
    for transition in semantics["positive_transitions"]:
        known.update(str(stage).casefold() for stage in transition)
    return known


def _terminal_status_resolved(stage: str, semantics: dict[str, object]) -> bool:
    return stage.casefold() in _known_terminal_status_stages(semantics)


def _group_unresolved_terminal_stages(
    records: list[dict[str, object]],
    semantics: dict[str, object],
    pre_range_stages: list[str] | None = None,
) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    observed_stages = list(pre_range_stages or [])
    observed_stages.extend(
        str(month["stage"])
        for record in records
        for month in record["months"]
    )
    for stage in observed_stages:
        if not stage.strip() or _terminal_status_resolved(stage, semantics):
            continue
        entry = grouped.setdefault(
            stage,
            {
                "stage": stage,
                "code": "unknown_terminal_status",
                "occurrences": 0,
                "affects_output": True,
            },
        )
        entry["occurrences"] = int(entry["occurrences"]) + 1
    return [
        grouped[stage]
        for stage in sorted(grouped, key=lambda value: (value.casefold(), value))
    ]


def analyze(
    source: Path,
    view: str,
    semantics_path: Path,
    mapping_path: Path | None = None,
    sheet: str | None = None,
    json_key: str | None = None,
    months: list[str] | None = None,
    filters_path: Path | None = None,
) -> dict[str, object]:
    """Analyze transition semantics without writing artifacts or exposing rows."""

    source = Path(source)
    semantics_path = Path(semantics_path)
    semantics = validate_semantics(_load_json_object(semantics_path, "semantics"))
    filters = _load_json_object(filters_path, "filters") if filters_path else {}
    rows, metadata = load_source(source, sheet, json_key)
    explicit_mapping = (
        _load_json_object(mapping_path, "mapping") if mapping_path is not None else None
    )
    mapping = resolve_mapping(list(rows[0]), explicit_mapping)
    selected_mapping, selected_months, first_selected_index = _selected_mapping(
        mapping, months
    )
    pre_range_months = list(mapping["months"])[:first_selected_index]
    pre_range_stages, pre_range_terminal_rows, pre_range_formula_errors = (
        _scan_pre_range_stages(rows, pre_range_months, semantics, metadata)
    )
    intersecting_formulas = [
        *pre_range_formula_errors,
        *_selected_formula_errors(
            metadata, mapping, selected_mapping, pre_range_terminal_rows
        ),
    ]
    if intersecting_formulas:
        raise DataContractError(
            "formula_cache_missing",
            "A required formula cell has no cached value",
            {"cells": intersecting_formulas},
        )

    range_exclusions = _terminal_before_range_exclusions(
        rows, list(mapping["months"]), first_selected_index, semantics
    )
    records, invalid_exclusions = normalize_rows(rows, selected_mapping, semantics)
    range_excluded_rows = {
        (item["id"], item["source_row"]) for item in range_exclusions
    }
    records = [
        record
        for record in records
        if (record["id"], record["source_row"]) not in range_excluded_rows
    ]
    invalid_exclusions = [
        item
        for item in invalid_exclusions
        if (item["id"], item["source_row"]) not in range_excluded_rows
    ]
    filtered_records, filter_exclusions = apply_filters(records, filters)
    select_records(filtered_records, view)
    unresolved_terminals = _group_unresolved_terminal_stages(
        filtered_records, semantics, pre_range_stages
    )
    unresolved = _group_transitions(
        filtered_records, view, "unknown", "unknown_transition", semantics
    )
    mixed = _group_transitions(
        filtered_records, view, "mixed", "mixed_signals", semantics
    )
    warning_count = sum(len(record["warnings"]) for record in filtered_records)

    return {
        "source": {
            "basename": metadata["source"],
            "sha256": metadata["sha256"],
            "format": metadata["format"],
            "sheet": metadata.get("sheet"),
            "json_key": metadata.get("json_key"),
        },
        "view": view,
        "mapping": selected_mapping,
        "selected_months": selected_months,
        "filter_keys": sorted(filters),
        "counts": {
            "source_rows": len(rows),
            "normalized_rows": len(records),
            "filtered_rows": len(filtered_records),
            "excluded_rows": len(range_exclusions)
            + len(invalid_exclusions)
            + len(filter_exclusions),
            "warnings": warning_count,
            "unresolved_terminal_stage_groups": len(unresolved_terminals),
            "unresolved_terminal_stage_occurrences": sum(
                int(item["occurrences"]) for item in unresolved_terminals
            ),
            "unresolved_transition_groups": len(unresolved),
            "unresolved_transition_occurrences": sum(
                int(item["occurrences"]) for item in unresolved
            ),
            "mixed_transition_groups": len(mixed),
            "mixed_transition_occurrences": sum(
                int(item["occurrences"]) for item in mixed
            ),
        },
        "unresolved_terminal_stages": unresolved_terminals,
        "unresolved_transitions": unresolved,
        "mixed_transitions": mixed,
    }


def prepare(
    source: Path,
    view: str,
    semantics_path: Path,
    output_dir: Path,
    mapping_path: Path | None = None,
    sheet: str | None = None,
    json_key: str | None = None,
    months: list[str] | None = None,
    filters_path: Path | None = None,
) -> dict[str, object]:
    """Normalize, filter, select, and atomically write preparation artifacts."""

    source = Path(source)
    output_dir = Path(output_dir)
    create_output_dir = _validate_output_destination(output_dir)

    semantics_path = Path(semantics_path)
    semantics = validate_semantics(_load_json_object(semantics_path, "semantics"))
    semantics_sha256 = hashlib.sha256(semantics_path.read_bytes()).hexdigest()
    filters = _load_json_object(filters_path, "filters") if filters_path else {}
    rows, metadata = load_source(source, sheet, json_key)
    explicit_mapping = (
        _load_json_object(mapping_path, "mapping") if mapping_path is not None else None
    )
    mapping = resolve_mapping(list(rows[0]), explicit_mapping)
    selected_mapping, selected_months, first_selected_index = _selected_mapping(
        mapping, months
    )
    pre_range_months = list(mapping["months"])[:first_selected_index]
    _, pre_range_terminal_rows, pre_range_formula_errors = _scan_pre_range_stages(
        rows, pre_range_months, semantics, metadata
    )
    intersecting_formulas = [
        *pre_range_formula_errors,
        *_selected_formula_errors(
            metadata, mapping, selected_mapping, pre_range_terminal_rows
        ),
    ]
    if intersecting_formulas:
        raise DataContractError(
            "formula_cache_missing",
            "A required formula cell has no cached value",
            {"cells": intersecting_formulas},
        )

    range_exclusions = _terminal_before_range_exclusions(
        rows, list(mapping["months"]), first_selected_index, semantics
    )
    records, invalid_exclusions = normalize_rows(
        rows, selected_mapping, semantics
    )
    range_excluded_rows = {
        (item["id"], item["source_row"]) for item in range_exclusions
    }
    records = [
        record
        for record in records
        if (record["id"], record["source_row"]) not in range_excluded_rows
    ]
    invalid_exclusions = [
        item
        for item in invalid_exclusions
        if (item["id"], item["source_row"]) not in range_excluded_rows
    ]
    warnings = [
        {
            "id": record["id"],
            "source_row": record["source_row"],
            **warning,
        }
        for record in records
        for warning in record["warnings"]
    ]
    filtered_records, filter_exclusions = apply_filters(records, filters)
    selected_records, view_exclusions = select_records(filtered_records, view)
    exclusions = (
        range_exclusions + invalid_exclusions + filter_exclusions + view_exclusions
    )
    counts = {
        "source_rows": len(rows),
        "normalized_rows": len(records),
        "included_rows": len(selected_records),
        "excluded_rows": len(exclusions),
        "warnings": len(warnings),
    }
    source_summary = {
        "source": metadata["source"],
        "sha256": metadata["sha256"],
        "format": metadata["format"],
        "sheet": metadata.get("sheet"),
        "mapping": mapping,
        "semantics_sha256": semantics_sha256,
        "selected_months": selected_months,
        "view": view,
        "filters": filters,
        "counts": counts,
    }
    normalized_data = {
        "schema_version": 1,
        "view": view,
        "source": {
            "basename": metadata["source"],
            "sha256": metadata["sha256"],
            "sheet": metadata.get("sheet"),
        },
        "mapping": mapping,
        "semantics": semantics,
        "selected_months": selected_months,
        "filters": filters,
        "records": selected_records,
        "exclusions": exclusions,
        "warnings": warnings,
        "counts": counts,
    }

    _write_artifacts(
        output_dir,
        [
            ("source-summary.json", source_summary),
            ("normalized-data.json", normalized_data),
            ("exclusions.json", {"exclusions": exclusions}),
        ],
        create_output_dir,
    )
    return {
        "output_dir": str(output_dir),
        "artifacts": [
            "source-summary.json",
            "normalized-data.json",
            "exclusions.json",
        ],
        "counts": counts,
    }


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise DataContractError("invalid_arguments", "Invalid command arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="prepare_opportunities.py")
    subcommands = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subcommands.add_parser("inspect")
    inspect_parser.add_argument("source", type=Path)
    inspect_parser.add_argument("--sheet")
    inspect_parser.add_argument("--json-key")
    analyze_parser = subcommands.add_parser("analyze")
    analyze_parser.add_argument("source", type=Path)
    analyze_parser.add_argument("--view", required=True)
    analyze_parser.add_argument("--semantics", required=True, type=Path)
    analyze_parser.add_argument("--mapping", type=Path)
    analyze_parser.add_argument("--sheet")
    analyze_parser.add_argument("--json-key")
    analyze_parser.add_argument("--months", action="append")
    analyze_parser.add_argument("--filters", type=Path)
    prepare_parser = subcommands.add_parser("prepare")
    prepare_parser.add_argument("source", type=Path)
    prepare_parser.add_argument("--view", required=True)
    prepare_parser.add_argument("--semantics", required=True, type=Path)
    prepare_parser.add_argument("--mapping", type=Path)
    prepare_parser.add_argument("--output-dir", required=True, type=Path)
    prepare_parser.add_argument("--sheet")
    prepare_parser.add_argument("--json-key")
    prepare_parser.add_argument("--months", action="append")
    prepare_parser.add_argument("--filters", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "inspect":
            report = inspect_source(args.source, args.sheet, args.json_key)
            print(json.dumps({"ok": True, **report}, separators=(",", ":")))
            return 0
        if args.command == "analyze":
            report = analyze(
                args.source,
                args.view,
                args.semantics,
                mapping_path=args.mapping,
                sheet=args.sheet,
                json_key=args.json_key,
                months=args.months,
                filters_path=args.filters,
            )
            print(json.dumps({"ok": True, **report}, separators=(",", ":")))
            return 0
        if args.command == "prepare":
            report = prepare(
                args.source,
                args.view,
                args.semantics,
                args.output_dir,
                mapping_path=args.mapping,
                sheet=args.sheet,
                json_key=args.json_key,
                months=args.months,
                filters_path=args.filters,
            )
            print(json.dumps({"ok": True, **report}, separators=(",", ":")))
            return 0
    except DataContractError as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": error.code,
                        "message": str(error),
                        "details": error.details,
                    },
                },
                separators=(",", ":"),
            )
        )
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
