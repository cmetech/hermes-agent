"""Portable workflow orchestration plugin.

The plugin exposes operator CLI commands and deliberately registers no
permanent model-facing tools.
"""

from __future__ import annotations


def register(ctx) -> None:
    from plugins.workflow.cli import register_cli, workflow_command

    ctx.register_cli_command(
        name="workflow",
        help="Inspect and operate portable workflows",
        setup_fn=register_cli,
        handler_fn=workflow_command,
        description="Discover, validate, inspect, trust, and operate portable workflows.",
    )
