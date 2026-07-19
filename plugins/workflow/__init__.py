"""Portable workflow orchestration plugin.

The plugin exposes operator CLI commands and deliberately registers no
permanent model-facing tools.
"""

from __future__ import annotations


def register(ctx) -> None:
    from plugins.workflow.cli import register_cli, workflow_command
    from plugins.workflow.coordinator import create_workflow_coordinator
    from plugins.workflow.gateway_command import workflow_gateway_command

    def handler(args):
        return workflow_command(
            args,
            agent_runner=ctx.agent,
            profile_name=ctx.profile_name,
        )

    ctx.register_cli_command(
        name="workflow",
        help="Inspect and operate portable workflows",
        setup_fn=register_cli,
        handler_fn=handler,
        description="Discover, validate, inspect, trust, and operate portable workflows.",
    )
    register_authenticated = getattr(
        type(ctx), "register_authenticated_command", None
    )
    if callable(register_authenticated):
        register_authenticated(
            ctx,
            "workflow",
            workflow_gateway_command,
            description="Start background workflows and decide workflow gates",
            args_hint="{run|approve|reject} ...",
        )
    ctx.register_background_service(
        "coordinator",
        create_workflow_coordinator,
        hosts={"web", "gateway"},
    )
