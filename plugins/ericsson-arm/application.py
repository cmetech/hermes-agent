"""Authority-neutral Artifactory application execution boundary."""

from __future__ import annotations

from . import tools as arm_tools
from .models import ArmError, SAFE_ERROR_MESSAGES, safe_remediation


def execute(
    name,
    arguments,
    configuration,
    *,
    cancel_check=None,
) -> dict:
    """Execute one validated ARM operation and return a safe envelope."""
    try:
        result = arm_tools.invoke(
            name,
            arguments,
            configuration,
            cancel_check=cancel_check,
        )
        return {"success": True, "result": result}
    except ArmError as exc:
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
