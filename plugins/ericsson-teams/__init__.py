"""ericsson-teams plugin — registers Teams/Graph tools into `ericsson-teams`."""
from __future__ import annotations

import os
import sys

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

import graph_auth  # noqa: E402
import teams_tools  # noqa: E402


def register(ctx) -> None:
    from tools.registry import tool_error, tool_result

    def _auth_handler(args: dict, **kw) -> str:
        try:
            if (args or {}).get("complete"):
                return tool_result(graph_auth.complete_device_flow())
            return tool_result(graph_auth.start_device_flow())
        except graph_auth.AuthRequired as e:
            return tool_error(str(e))
        except ImportError:
            return tool_error("msal is not installed in the Hermes environment — "
                              "run: pip install msal")

    def _wrap(fn):
        def handler(args: dict, **kw) -> str:
            try:
                return tool_result({"result": fn(**(args or {}))})
            except (teams_tools.TeamsError, graph_auth.AuthRequired) as e:
                return tool_error(str(e))
            except ImportError:
                return tool_error("msal is not installed in the Hermes environment — "
                                  "run: pip install msal")
            except TypeError as e:
                return tool_error(f"bad arguments: {e}")
            except Exception as e:
                return tool_error(f"teams tool failed: {type(e).__name__}: {e}")
        return handler

    handlers = {
        "teams_auth": _auth_handler,
        "teams_list": _wrap(teams_tools.teams_list),
        "teams_channels": _wrap(teams_tools.teams_channels),
        "teams_read": _wrap(teams_tools.teams_read),
        "teams_send": _wrap(teams_tools.teams_send),
        "teams_reply": _wrap(teams_tools.teams_reply),
    }
    for name, schema in teams_tools.SCHEMAS.items():
        ctx.register_tool(name=name, toolset="ericsson-teams", schema=schema,
                          handler=handlers[name],
                          check_fn=teams_tools.check_available, emoji="💬")
