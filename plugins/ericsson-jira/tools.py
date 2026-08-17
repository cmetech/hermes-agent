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
    "jira_list_fields": {
        "name": "jira_list_fields",
        "description": (
            "List Jira field IDs and names, so custom field identifiers such "
            "as customfield_10234 can be resolved before reading or writing "
            "them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "custom_only": {"type": "boolean", "default": False},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "additionalProperties": False,
        },
    },
    "jira_get_project": {
        "name": "jira_get_project",
        "description": (
            "Fetch one Jira project's issue types, components and versions — "
            "the metadata required to create or edit an issue in it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "minLength": 1, "maxLength": 64}
            },
            "required": ["key"],
            "additionalProperties": False,
        },
    },
    "jira_list_transitions": {
        "name": "jira_list_transitions",
        "description": (
            "List the workflow transitions currently available on a Jira "
            "issue. Required before transitioning: transition IDs are "
            "workflow-specific and cannot be guessed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "minLength": 3, "maxLength": 128}
            },
            "required": ["key"],
            "additionalProperties": False,
        },
    },
    "jira_search_assignable_users": {
        "name": "jira_search_assignable_users",
        "description": (
            "Find users who can be assigned issues in one Jira project. "
            "Assignability is a per-project permission, so a valid Jira user "
            "may still be unassignable here."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "minLength": 1, "maxLength": 64},
                "query": {"type": "string", "maxLength": 255},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["project"],
            "additionalProperties": False,
        },
    },
    "jira_transition_issue": {
        "name": "jira_transition_issue",
        "description": (
            "Move a Jira issue through one workflow transition. Call "
            "jira_list_transitions first to obtain a valid transition_id. "
            "Requires dry_run or confirm."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "minLength": 3, "maxLength": 128},
                "transition_id": {
                    "type": "string",
                    "pattern": "^[0-9]{1,19}$",
                },
                "expected_status": {"type": "string", "maxLength": 255},
                "dry_run": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["key", "transition_id"],
            "additionalProperties": False,
        },
    },
    "jira_assign_issue": {
        "name": "jira_assign_issue",
        "description": (
            "Assign a Jira issue to a user, or unassign it with assignee null. "
            "Use jira_search_assignable_users to find a valid identifier — "
            "assignability is a per-project permission. Requires dry_run or "
            "confirm."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "minLength": 3, "maxLength": 128},
                "assignee": {"type": ["string", "null"], "maxLength": 255},
                "dry_run": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["key", "assignee"],
            "additionalProperties": False,
        },
    },
    "jira_update_fields": {
        "name": "jira_update_fields",
        "description": (
            "Set fields on a Jira issue. Accepts summary, description, "
            "priority, duedate, labels, environment, and customfield_* IDs "
            "resolved via jira_list_fields. Requires dry_run or confirm."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "minLength": 3, "maxLength": 128},
                "fields": {
                    "type": "object",
                    "minProperties": 1,
                    "maxProperties": 20,
                },
                "dry_run": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["key", "fields"],
            "additionalProperties": False,
        },
    },
    "jira_manage_labels": {
        "name": "jira_manage_labels",
        "description": (
            "Add or remove labels on a Jira issue. Labels cannot contain "
            "whitespace. Requires dry_run or confirm."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "minLength": 3, "maxLength": 128},
                "operation": {"type": "string", "enum": ["add", "remove"]},
                "labels": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 255},
                    "minItems": 1,
                    "maxItems": 50,
                },
                "dry_run": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["key", "operation", "labels"],
            "additionalProperties": False,
        },
    },
    "jira_create_issue": {
        "name": "jira_create_issue",
        "description": (
            "Create one Jira issue. Use jira_get_project first to obtain a "
            "valid issue type name for the project. Requires dry_run or "
            "confirm."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "minLength": 1, "maxLength": 64},
                "issue_type": {"type": "string", "minLength": 1, "maxLength": 255},
                "summary": {"type": "string", "minLength": 1, "maxLength": 255},
                "description": {"type": "string", "maxLength": 32_000},
                "dry_run": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["project", "issue_type", "summary"],
            "additionalProperties": False,
        },
    },
    "jira_list_link_types": {
        "name": "jira_list_link_types",
        "description": (
            "List the issue link types configured on this Jira instance, "
            "including their inward and outward directional phrasings."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    "jira_link_issues": {
        "name": "jira_link_issues",
        "description": (
            "Link two Jira issues. Direction is not symmetric: for type "
            "'Blocks', the inward issue is blocked by the outward issue. "
            "Use jira_list_link_types first. Requires dry_run or confirm."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "inward": {"type": "string", "minLength": 3, "maxLength": 128},
                "outward": {"type": "string", "minLength": 3, "maxLength": 128},
                "link_type": {"type": "string", "minLength": 1, "maxLength": 255},
                "dry_run": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["inward", "outward", "link_type"],
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
    if type(args) is not dict:
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
        "jira_list_fields": {"custom_only", "max_results"},
        "jira_get_project": {"key"},
        "jira_list_transitions": {"key"},
        "jira_search_assignable_users": {"project", "query", "max_results"},
        "jira_transition_issue": {
            "key",
            "transition_id",
            "expected_status",
            "dry_run",
            "confirm",
        },
        "jira_assign_issue": {"key", "assignee", "dry_run", "confirm"},
        "jira_update_fields": {"key", "fields", "dry_run", "confirm"},
        "jira_manage_labels": {
            "key",
            "operation",
            "labels",
            "dry_run",
            "confirm",
        },
        "jira_create_issue": {
            "project",
            "issue_type",
            "summary",
            "description",
            "dry_run",
            "confirm",
        },
        "jira_list_link_types": set(),
        "jira_link_issues": {
            "inward",
            "outward",
            "link_type",
            "dry_run",
            "confirm",
        },
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
            "jira_list_fields": operations.list_fields,
            "jira_get_project": operations.get_project,
            "jira_list_transitions": operations.list_transitions,
            "jira_search_assignable_users": operations.search_assignable_users,
            "jira_transition_issue": operations.transition_issue,
            "jira_assign_issue": operations.assign_issue,
            "jira_update_fields": operations.update_fields,
            "jira_manage_labels": operations.manage_labels,
            "jira_create_issue": operations.create_issue,
            "jira_list_link_types": operations.list_link_types,
            "jira_link_issues": operations.link_issues,
        }
        handler = handlers.get(name)
        return handler(**dict(args))
