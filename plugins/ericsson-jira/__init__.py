"""ericsson-jira plugin — registers Jira tools into the `ericsson-jira` toolset.

Loaded by the Hermes plugin loader (bundled or $HERMES_HOME/plugins). The
sys.path insert makes `jira_tools` importable regardless of how the loader
materializes this package (bundled package vs. user-plugin dir).
"""
from __future__ import annotations

import os
import sys

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

import jira_tools  # noqa: E402


def register(ctx) -> None:
    from tools.registry import tool_error, tool_result  # Hermes runtime only

    def _wrap(fn):
        def handler(args: dict, **kw) -> str:
            try:
                return tool_result({"result": fn(**(args or {}))})
            except jira_tools.JiraError as e:
                return tool_error(str(e))
            except TypeError as e:
                return tool_error(f"bad arguments: {e}")
            except Exception as e:
                return tool_error(f"jira tool failed: {type(e).__name__}: {e}")
        return handler

    handlers = {
        "jira_my_tickets": _wrap(jira_tools.my_tickets),
        "jira_get_issue": _wrap(jira_tools.get_issue),
        "jira_add_comment": _wrap(jira_tools.add_comment),
    }
    for name, schema in jira_tools.SCHEMAS.items():
        ctx.register_tool(name=name, toolset="ericsson-jira", schema=schema,
                          handler=handlers[name],
                          check_fn=jira_tools.check_available, emoji="🎫")
