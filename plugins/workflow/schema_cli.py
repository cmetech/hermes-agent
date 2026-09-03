"""Bounded, side-effect-free CLI authority for workflow schema output."""

from __future__ import annotations

import argparse
import json
from typing import Callable

from plugins.workflow.language_conformance import workflow_language_conformance
from plugins.workflow.language_schema import workflow_authoring_contract
from plugins.workflow.models import WorkflowLanguageProfile


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
) -> int:
    """Emit one deterministic authoring-data envelope."""
    payload = producer(WorkflowLanguageProfile(args.profile))
    print(
        json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":") if args.json else None,
            indent=None if args.json else 2,
        )
    )
    return 0


def emit_schema(args: argparse.Namespace) -> int:
    """Emit one deterministic workflow authoring contract."""
    return emit_authoring_json(args, workflow_authoring_contract)


def emit_schema_corpus(args: argparse.Namespace) -> int:
    """Emit one deterministic workflow authoring conformance corpus."""
    return emit_authoring_json(args, workflow_language_conformance)
