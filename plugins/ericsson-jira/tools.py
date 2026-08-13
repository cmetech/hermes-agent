"""Public Jira tool schemas and configuration-bound invocation."""

from __future__ import annotations

from typing import Any, Mapping

if __package__:
    from .auth import authentication_from_configuration
    from .client import JiraClient
    from .models import JiraAuth, JiraError
    from .operations import JiraOperations, SAFE_FIELDS
else:
    from auth import authentication_from_configuration
    from client import JiraClient
    from models import JiraAuth, JiraError
    from operations import JiraOperations, SAFE_FIELDS


_STRING = {"type": "string", "minLength": 1, "maxLength": 4096}
_FILTER = {
    "type": "array",
    "items": {"type": "string", "minLength": 1, "maxLength": 128},
    "maxItems": 20,
}
_LIMIT = {"type": "integer", "minimum": 1, "maximum": 100}
_AGE = {"type": "integer", "minimum": 0, "maximum": 3650}

SCHEMAS = {
    "jira_my_tickets": {
        "name": "jira_my_tickets",
        "description": "List bounded unresolved Jira tickets assigned to the current user.",
        "parameters": {
            "type": "object",
            "properties": {"max_results": _LIMIT},
            "additionalProperties": False,
        },
    },
    "jira_search_issues": {
        "name": "jira_search_issues",
        "description": "Search bounded Jira issue evidence using explicit JQL and safe fields.",
        "parameters": {
            "type": "object",
            "properties": {
                "jql": _STRING,
                "max_results": _LIMIT,
                "fields": {
                    "type": "array",
                    "items": {"type": "string", "enum": sorted(SAFE_FIELDS)},
                    "minItems": 1,
                    "maxItems": len(SAFE_FIELDS),
                },
                "statuses": _FILTER,
                "issue_types": _FILTER,
                "priorities": _FILTER,
                "labels": _FILTER,
                "min_age_days": _AGE,
                "max_age_days": _AGE,
            },
            "required": ["jql", "max_results"],
            "additionalProperties": False,
        },
    },
    "jira_get_issue": {
        "name": "jira_get_issue",
        "description": "Fetch bounded normalized Jira issue context and five recent comments.",
        "parameters": {
            "type": "object",
            "properties": {"key": {"type": "string", "minLength": 3, "maxLength": 128}},
            "required": ["key"],
            "additionalProperties": False,
        },
    },
    "jira_add_comment": {
        "name": "jira_add_comment",
        "description": "Add one bounded comment to a Jira issue after host approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "minLength": 3, "maxLength": 128},
                "body": {"type": "string", "minLength": 1, "maxLength": 32000},
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["key", "body"],
            "additionalProperties": False,
        },
    },
}


def check_available(configuration=None) -> bool:
    if configuration is None:
        return False
    try:
        authentication_from_configuration(configuration)
        return True
    except JiraError:
        return False


def client_from_configuration(configuration, **options) -> JiraClient:
    return JiraClient(authentication_from_configuration(configuration), **options)


def invoke(name: str, args: Mapping[str, Any], configuration, **client_options):
    if not isinstance(args, Mapping):
        raise JiraError("invalid_input")
    allowed_arguments = {
        "jira_my_tickets": {"max_results"},
        "jira_search_issues": {
            "jql",
            "max_results",
            "fields",
            "statuses",
            "issue_types",
            "priorities",
            "labels",
            "min_age_days",
            "max_age_days",
        },
        "jira_get_issue": {"key"},
        "jira_add_comment": {"key", "body", "dry_run"},
    }
    if name not in allowed_arguments or not set(args).issubset(allowed_arguments[name]):
        raise JiraError("invalid_input")
    with client_from_configuration(configuration, **client_options) as client:
        operations = JiraOperations(client)
        handlers = {
            "jira_my_tickets": operations.my_tickets,
            "jira_search_issues": operations.search_issues,
            "jira_get_issue": operations.get_issue,
            "jira_add_comment": operations.add_comment,
        }
        handler = handlers.get(name)
        return handler(**dict(args))
