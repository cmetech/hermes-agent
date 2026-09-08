"""Bounded, side-effect-free CLI authority for workflow schema output."""

from __future__ import annotations

import argparse
import json
from typing import Callable

from plugins.workflow.language_conformance import workflow_language_conformance
from plugins.workflow.language_schema import (
    canonical_contract_json,
    workflow_authoring_contract,
)
from plugins.workflow.models import WorkflowLanguageProfile


SCHEMA_CORPUS_MAX_CASES = 64
SCHEMA_CORPUS_MAX_BYTES = 160_000
ARCHON_SCHEMA_CORPUS_SECTION_LIMITS = {
    "cases": 64,
    "scanner_cases": 256,
    "substitution_cases": 64,
    "structured_path_cases": 64,
}
ARCHON_SCHEMA_CORPUS_MAX_CANONICAL_BYTES = 384_000
ARCHON_SCHEMA_CORPUS_MAX_EMITTED_BYTES = 768_000


def _validate_case_section(
    payload: dict[str, object], section: str, limit: int
) -> None:
    cases = payload.get(section)
    if not isinstance(cases, list):
        raise ValueError(f"schema corpus {section} must be a list")
    if len(cases) > limit:
        raise ValueError(
            f"schema corpus {section} has more than {limit} cases"
        )


def _validate_schema_corpus_structure(
    payload: dict[str, object],
    *,
    profile: WorkflowLanguageProfile,
) -> int:
    """Validate trusted format and section structure before serialization."""
    format_version = payload.get("format_version")
    if type(format_version) is not int or format_version not in (1, 2):
        raise ValueError(
            f"schema corpus has unsupported format_version: {format_version!r}"
        )
    expected_format = (
        2 if profile is WorkflowLanguageProfile.ARCHON_2026_07 else 1
    )
    if format_version != expected_format:
        raise ValueError(
            f"schema corpus format_version {format_version} is unsupported "
            f"for profile {profile.value}"
        )

    if format_version == 1:
        cases = payload.get("cases")
        if not isinstance(cases, list):
            raise ValueError("schema corpus cases must be a list")
        if len(cases) > SCHEMA_CORPUS_MAX_CASES:
            raise ValueError(
                f"schema corpus has more than {SCHEMA_CORPUS_MAX_CASES} cases"
            )
    else:
        for section, limit in ARCHON_SCHEMA_CORPUS_SECTION_LIMITS.items():
            _validate_case_section(payload, section, limit)
    return format_version


def _validate_schema_corpus_bytes(
    format_version: int, *, canonical_bytes: int, emitted_bytes: int
) -> None:
    """Validate byte bounds after trusted structure has been established."""
    if format_version == 1:
        if (
            canonical_bytes > SCHEMA_CORPUS_MAX_BYTES
            or emitted_bytes > SCHEMA_CORPUS_MAX_BYTES
        ):
            raise ValueError(
                f"schema corpus has more than {SCHEMA_CORPUS_MAX_BYTES} bytes"
            )
        return

    if canonical_bytes > ARCHON_SCHEMA_CORPUS_MAX_CANONICAL_BYTES:
        raise ValueError(
            "schema corpus has more than "
            f"{ARCHON_SCHEMA_CORPUS_MAX_CANONICAL_BYTES} canonical bytes"
        )
    if emitted_bytes > ARCHON_SCHEMA_CORPUS_MAX_EMITTED_BYTES:
        raise ValueError(
            "schema corpus has more than "
            f"{ARCHON_SCHEMA_CORPUS_MAX_EMITTED_BYTES} emitted bytes"
        )


def _encode_authoring_json(
    args: argparse.Namespace, payload: dict[str, object]
) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":") if args.json else None,
        indent=None if args.json else 2,
    )


def configure_schema_parser(parser: argparse.ArgumentParser) -> None:
    """Register the complete child grammar shared by packaged and plugin CLIs."""
    parser.add_argument(
        "--profile",
        choices=tuple(profile.value for profile in WorkflowLanguageProfile),
        default=WorkflowLanguageProfile.ARCHON_2026_07.value,
    )
    parser.add_argument("--json", action="store_true", help="Emit stable JSON output")


def emit_authoring_json(
    args: argparse.Namespace,
    producer: Callable[[WorkflowLanguageProfile], dict[str, object]],
    *,
    max_cases: int | None = None,
    max_bytes: int | None = None,
) -> int:
    """Emit one deterministic authoring-data envelope."""
    selected_profile = WorkflowLanguageProfile(args.profile)
    payload = producer(selected_profile)
    encoded = _encode_authoring_json(args, payload)
    if max_cases is not None:
        cases = payload.get("cases")
        if not isinstance(cases, list):
            raise ValueError("schema corpus cases must be a list")
        if len(cases) > max_cases:
            raise ValueError(f"schema corpus has more than {max_cases} cases")
    if max_bytes is not None and len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError(f"schema corpus has more than {max_bytes} bytes")
    print(encoded)
    return 0


def emit_schema(args: argparse.Namespace) -> int:
    """Emit one deterministic workflow authoring contract."""
    return emit_authoring_json(args, workflow_authoring_contract)


def emit_schema_corpus(args: argparse.Namespace) -> int:
    """Emit one deterministic workflow authoring conformance corpus."""
    selected_profile = WorkflowLanguageProfile(args.profile)
    payload = workflow_language_conformance(selected_profile)
    format_version = _validate_schema_corpus_structure(
        payload, profile=selected_profile
    )
    canonical_bytes = len(canonical_contract_json(payload).encode("utf-8"))
    encoded = _encode_authoring_json(args, payload)
    _validate_schema_corpus_bytes(
        format_version,
        canonical_bytes=canonical_bytes,
        emitted_bytes=len(encoded.encode("utf-8")),
    )
    print(encoded)
    return 0
