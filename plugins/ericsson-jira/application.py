"""Authority-neutral Jira application execution boundary."""

from __future__ import annotations

from . import tools as jira_tools
from .models import JiraError, SAFE_ERROR_MESSAGES, safe_remediation


def execute(
    name,
    arguments,
    configuration,
    *,
    cancel_check=None,
) -> dict:
    """Execute one validated Jira operation and return a safe envelope."""
    try:
        result = jira_tools.invoke(
            name,
            arguments,
            configuration,
            cancel_check=cancel_check,
        )
        return {"success": True, "result": result}
    except JiraError as exc:
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
