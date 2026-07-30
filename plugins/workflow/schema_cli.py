"""Bounded, side-effect-free CLI authority for workflow schema output."""

from __future__ import annotations

import argparse
import json

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


def emit_schema(args: argparse.Namespace) -> int:
    """Emit one deterministic workflow authoring contract."""
    contract = workflow_authoring_contract(WorkflowLanguageProfile(args.profile))
    if args.json:
        print(
            json.dumps(
                contract,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    else:
        print(json.dumps(contract, sort_keys=True, ensure_ascii=False, indent=2))
    return 0
