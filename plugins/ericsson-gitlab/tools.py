"""Tool schemas and safe invocation adapters for bounded GitLab reads."""

from __future__ import annotations

from typing import Any, Mapping

if __package__:
    from .auth import GitLabAuth
    from .client import GitLabClient
    from .models import GitLabError
    from .operations import GitLabOperations
else:  # Standalone source tests import modules directly from the plugin root.
    from auth import GitLabAuth
    from client import GitLabClient
    from models import GitLabError
    from operations import GitLabOperations


_PROJECT = {
    "oneOf": [
        {"type": "string", "minLength": 1, "maxLength": 2048},
        {"type": "integer", "minimum": 1},
    ],
    "description": (
        "Project numeric id, namespace/project slug, or URL on the configured "
        "GitLab origin."
    ),
}
_REF = {"type": "string", "minLength": 1, "maxLength": 512}
_PATH = {"type": "string", "maxLength": 4096}


def _schema(name: str, description: str, properties: dict, required: list[str]):
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


SCHEMAS = {
    "gitlab_resolve_project": _schema(
        "gitlab_resolve_project",
        "Resolve one GitLab project and an optional repository tree/blob link "
        "to bounded canonical identity.",
        {"project": _PROJECT},
        ["project"],
    ),
    "gitlab_list_repository_tree": _schema(
        "gitlab_list_repository_tree",
        "List a bounded, normalized repository tree at one explicit ref.",
        {
            "project": _PROJECT,
            "ref": _REF,
            "path": _PATH,
            "recursive": {"type": "boolean"},
            "max_items": {"type": "integer", "minimum": 1, "maximum": 2000},
        },
        ["project", "ref"],
    ),
    "gitlab_read_file": _schema(
        "gitlab_read_file",
        "Read one bounded UTF-8 repository file or return safe binary metadata.",
        {
            "project": _PROJECT,
            "file_path": {**_PATH, "minLength": 1},
            "ref": _REF,
            "max_bytes": {"type": "integer", "minimum": 1, "maximum": 524288},
        },
        ["project", "file_path", "ref"],
    ),
    "gitlab_read_merge_request": _schema(
        "gitlab_read_merge_request",
        "Read bounded merge-request metadata and structured diffs.",
        {
            "project": _PROJECT,
            "iid": {"type": "integer", "minimum": 1, "maximum": 2147483647},
        },
        ["project", "iid"],
    ),
    "gitlab_list_pipelines": _schema(
        "gitlab_list_pipelines",
        "List bounded pipeline summaries without CI variables or job details.",
        {
            "project": _PROJECT,
            "ref": _REF,
            "status": {"type": "string", "minLength": 1, "maxLength": 64},
            "max_items": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        ["project"],
    ),
    "gitlab_inspect_ci": _schema(
        "gitlab_inspect_ci",
        "Inspect bounded GitLab pipeline, CI include, and variable metadata "
        "without exposing variable values or evaluating CI configuration.",
        {
            "project": _PROJECT,
            "branch_spec": _REF,
            "lookback_days": {"type": "integer", "minimum": 1, "maximum": 365},
            "collect_variables": {"type": "boolean"},
            "max_branches": {"type": "integer", "minimum": 1, "maximum": 100},
            "max_pages": {"type": "integer", "minimum": 1, "maximum": 10},
            "max_includes": {"type": "integer", "minimum": 1, "maximum": 100},
            "max_include_bytes": {
                "type": "integer",
                "minimum": 1,
                "maximum": 524288,
            },
            "max_groups": {"type": "integer", "minimum": 1, "maximum": 20},
            "max_variables": {"type": "integer", "minimum": 1, "maximum": 2000},
        },
        ["project"],
    ),
    "gitlab_create_branch": _schema(
        "gitlab_create_branch",
        "Create or reuse one bounded ticket branch after host approval.",
        {
            "project": _PROJECT,
            "prefix": {
                "type": "string",
                "minLength": 1,
                "maxLength": 512,
                "default": "fix",
                "description": "Branch prefix; defaults to fix.",
            },
            "ticket_key": {"type": "string", "minLength": 1, "maxLength": 128},
            "summary": {"type": "string", "minLength": 1, "maxLength": 2048},
            "source_ref": _REF,
            "dry_run": {"type": "boolean"},
        },
        ["project", "ticket_key", "summary"],
    ),
    "gitlab_commit_changes": _schema(
        "gitlab_commit_changes",
        "Apply one bounded atomic GitLab commit after host approval.",
        {
            "project": _PROJECT,
            "branch": _REF,
            "commit_message": {"type": "string", "minLength": 1, "maxLength": 4096},
            "actions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 100,
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["create", "update", "delete"],
                        },
                        "file_path": {**_PATH, "minLength": 1},
                        "content": {"type": "string", "maxLength": 524288},
                        "last_commit_id": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 512,
                        },
                    },
                    "required": ["action", "file_path"],
                    "additionalProperties": False,
                },
            },
            "dry_run": {"type": "boolean"},
        },
        ["project", "branch", "commit_message", "actions"],
    ),
    "gitlab_create_merge_request": _schema(
        "gitlab_create_merge_request",
        "Create or reuse one open GitLab merge request after host approval.",
        {
            "project": _PROJECT,
            "source_branch": _REF,
            "target_branch": _REF,
            "title": {"type": "string", "minLength": 1, "maxLength": 1024},
            "description": {"type": "string", "maxLength": 65536},
            "remove_source_branch": {"type": "boolean"},
            "squash": {"type": "boolean"},
            "dry_run": {"type": "boolean"},
        },
        ["project", "source_branch"],
    ),
}


def operations_from_configuration(configuration, **client_options) -> GitLabOperations:
    authentication = GitLabAuth.from_configuration(configuration)
    return GitLabOperations(GitLabClient(authentication, **client_options))


def invoke(name: str, args: Mapping[str, Any], configuration, **client_options):
    if name not in SCHEMAS or not isinstance(args, Mapping):
        raise GitLabError("invalid_input")
    parameters = SCHEMAS[name]["parameters"]
    allowed = set(parameters["properties"])
    required = set(parameters.get("required", ()))
    if (
        any(not isinstance(key, str) for key in args)
        or not required.issubset(args)
        or not set(args).issubset(allowed)
    ):
        raise GitLabError("invalid_input")
    operations = operations_from_configuration(configuration, **client_options)
    values = dict(args)
    try:
        if name == "gitlab_resolve_project":
            return operations.resolve_project(values["project"])
        if name == "gitlab_list_repository_tree":
            return operations.list_repository_tree(
                values["project"],
                ref=values["ref"],
                path=values.get("path", ""),
                recursive=values.get("recursive", False),
                max_items=values.get("max_items", 200),
            )
        if name == "gitlab_read_file":
            return operations.read_file(
                values["project"],
                values["file_path"],
                ref=values["ref"],
                max_bytes=values.get("max_bytes", 100 * 1024),
            )
        if name == "gitlab_read_merge_request":
            return operations.read_merge_request(values["project"], values["iid"])
        if name == "gitlab_inspect_ci":
            return operations.inspect_ci(
                values["project"],
                branch_spec=values.get("branch_spec", "RECENT"),
                lookback_days=values.get("lookback_days", 10),
                collect_variables=values.get("collect_variables", True),
                max_branches=values.get("max_branches", 20),
                max_pages=values.get("max_pages", 5),
                max_includes=values.get("max_includes", 20),
                max_include_bytes=values.get("max_include_bytes", 128 * 1024),
                max_groups=values.get("max_groups", 10),
                max_variables=values.get("max_variables", 500),
            )
        if name == "gitlab_create_branch":
            return operations.create_branch(
                values["project"],
                prefix=values.get("prefix", "fix"),
                ticket_key=values["ticket_key"],
                summary=values["summary"],
                source_ref=values.get("source_ref"),
                dry_run=values.get("dry_run", False),
            )
        if name == "gitlab_commit_changes":
            return operations.commit_changes(
                values["project"],
                branch=values["branch"],
                commit_message=values["commit_message"],
                actions=values["actions"],
                dry_run=values.get("dry_run", False),
            )
        if name == "gitlab_create_merge_request":
            return operations.create_merge_request(
                values["project"],
                source_branch=values["source_branch"],
                target_branch=values.get("target_branch"),
                title=values.get("title"),
                description=values.get("description", ""),
                remove_source_branch=values.get("remove_source_branch", True),
                squash=values.get("squash", False),
                dry_run=values.get("dry_run", False),
            )
        return operations.list_pipelines(
            values["project"],
            ref=values.get("ref"),
            status=values.get("status"),
            max_items=values.get("max_items", 50),
        )
    finally:
        operations.client.close()
