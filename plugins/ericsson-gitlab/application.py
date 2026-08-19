"""Authority-neutral GitLab application execution boundary."""

from __future__ import annotations

from . import tools as gitlab_tools
from ._common.errors import remediation_for
from .models import GitLabError, SAFE_ERROR_MESSAGES


_SAFE_REMEDIATIONS = frozenset(
    remediation
    for category in SAFE_ERROR_MESSAGES
    if (remediation := remediation_for(category, "gitlab")) is not None
)


def _safe_remediation(value: object) -> str | None:
    """Return only static, connector-owned remediation guidance."""
    if type(value) is not str or value not in _SAFE_REMEDIATIONS:
        return None
    return value


def execute(
    name,
    arguments,
    configuration,
    *,
    cancel_check=None,
) -> dict:
    """Execute one validated GitLab operation and return a safe envelope."""
    try:
        result = gitlab_tools.invoke(
            name,
            arguments,
            configuration,
            cancel_check=cancel_check,
        )
        return {"success": True, "result": result}
    except GitLabError as exc:
        error = {
            "category": exc.category,
            "message": SAFE_ERROR_MESSAGES[exc.category],
        }
        remediation = _safe_remediation(getattr(exc, "remediation", None))
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
