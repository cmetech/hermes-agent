"""Authority-neutral Confluence application execution boundary."""

from __future__ import annotations

from . import tools as confluence_tools
from .models import ConfluenceError, SAFE_ERROR_MESSAGES, safe_remediation


def execute(
    name,
    arguments,
    configuration,
    *,
    cancel_check=None,
) -> dict:
    """Execute one validated Confluence operation and return a safe envelope."""
    try:
        result = confluence_tools.invoke(
            name,
            arguments,
            configuration,
            cancel_check=cancel_check,
        )
        return {"success": True, "result": result}
    except ConfluenceError as exc:
        error = {
            "category": exc.category,
            "message": SAFE_ERROR_MESSAGES[exc.category],
        }
        remediation = safe_remediation(getattr(exc, "remediation", None))
        if remediation:
            error["remediation"] = remediation
        return {"success": False, "error": error}
    except (KeyError, TypeError, ValueError):
        return {
            "success": False,
            "error": {
                "category": "invalid_input",
                "message": SAFE_ERROR_MESSAGES["invalid_input"],
            },
        }
    except Exception:
        return {
            "success": False,
            "error": {
                "category": "transient",
                "message": SAFE_ERROR_MESSAGES["transient"],
            },
        }
