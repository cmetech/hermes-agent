"""Always-visible Ericsson connector command facade registration."""

from __future__ import annotations

from . import io as local_io
from . import parser
from . import render
from .descriptors import DESCRIPTORS


build_parser = parser.build_parser


def _setup_domain(domain_parser, *, domain: str, ctx) -> None:
    """Populate one reserved connector command tree."""
    parser.add_domain_commands(domain_parser, domain, ctx)


def register(ctx) -> None:
    """Atomically reserve all Ericsson connector top-level command domains."""
    for domain in ("jira", "gitlab", "confluence", "arm"):
        ctx.register_cli_command(
            name=domain,
            help=f"Run bounded Ericsson {domain.title()} connector commands",
            setup_fn=lambda domain_parser, domain=domain: _setup_domain(
                domain_parser, domain=domain, ctx=ctx
            ),
        )


__all__ = ["DESCRIPTORS", "build_parser", "register"]
