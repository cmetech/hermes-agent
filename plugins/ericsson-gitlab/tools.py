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
_GROUP = {
    "oneOf": [
        {"type": "string", "minLength": 1, "maxLength": 2048},
        {"type": "integer", "minimum": 1},
    ],
    "description": (
        "Group numeric id, full nested group path, or group URL on the "
        "configured GitLab origin."
    ),
}
_PAGE_CONTINUATION = {
    "type": "object",
    "properties": {
        "page": {"type": "integer", "minimum": 1},
        "next_page": {"type": "integer", "minimum": 1},
        "offset": {"type": "integer", "minimum": 0, "maximum": 99},
    },
    "additionalProperties": False,
}
_REF = {"type": "string", "minLength": 1, "maxLength": 512}
_PATH = {"type": "string", "maxLength": 4096}
_TIMESTAMP = {"type": "string", "minLength": 1, "maxLength": 128}


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
    "gitlab_list_group_projects": _schema(
        "gitlab_list_group_projects",
        "Explore a bounded GitLab group hierarchy and its visible projects, "
        "including empty subgroups and source-labelled continuation.",
        {
            "group": _GROUP,
            "recursive": {"type": "boolean"},
            "include_shared": {"type": "boolean"},
            "include_archived": {"type": "boolean"},
            "search": {"type": "string", "minLength": 1, "maxLength": 512},
            "max_groups": {"type": "integer", "minimum": 1, "maximum": 2000},
            "max_projects": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5000,
            },
            "continuation": {
                "type": "object",
                "properties": {
                    "groups": _PAGE_CONTINUATION,
                    "projects": _PAGE_CONTINUATION,
                },
                "additionalProperties": False,
            },
        },
        ["group"],
    ),
    "gitlab_list_commits": _schema(
        "gitlab_list_commits",
        "List bounded Git commit history newest-first for a project, ref, path, "
        "or rolling UTC time window. This is commit history, not pipelines.",
        {
            "project": _PROJECT,
            "ref": _REF,
            "path": {**_PATH, "minLength": 1},
            "since": _TIMESTAMP,
            "until": _TIMESTAMP,
            "lookback_hours": {
                "type": "integer",
                "minimum": 1,
                "maximum": 8760,
            },
            "max_items": {"type": "integer", "minimum": 1, "maximum": 2000},
            "continuation": _PAGE_CONTINUATION,
        },
        ["project"],
    ),
    "gitlab_read_commit": _schema(
        "gitlab_read_commit",
        "Read one bounded GitLab commit with canonical identity and aggregate stats.",
        {
            "project": _PROJECT,
            "commit": _REF,
        },
        ["project", "commit"],
    ),
    "gitlab_list_commit_comments": _schema(
        "gitlab_list_commit_comments",
        "List bounded display-safe comments attached to one GitLab commit, branch, or tag.",
        {
            "project": _PROJECT,
            "commit": _REF,
            "max_items": {"type": "integer", "minimum": 1, "maximum": 2000},
            "continuation": _PAGE_CONTINUATION,
        },
        ["project", "commit"],
    ),
    "gitlab_list_commit_discussions": _schema(
        "gitlab_list_commit_discussions",
        "List bounded threaded discussions and display-safe notes for one GitLab commit.",
        {
            "project": _PROJECT,
            "commit": _REF,
            "max_discussions": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000,
            },
            "max_notes_per_discussion": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
            },
            "continuation": _PAGE_CONTINUATION,
        },
        ["project", "commit"],
    ),
    "gitlab_list_merge_requests": _schema(
        "gitlab_list_merge_requests",
        "Explore bounded GitLab merge requests. Rolling lookback means newly "
        "created MRs; updated_after means recently active MRs.",
        {
            "project": _PROJECT,
            "state": {
                "type": "string",
                "enum": ["open", "opened", "closed", "merged", "all"],
            },
            "source_branch": _REF,
            "target_branch": _REF,
            "search": {"type": "string", "minLength": 1, "maxLength": 512},
            "order_by": {
                "type": "string",
                "enum": ["created_at", "updated_at"],
            },
            "sort": {"type": "string", "enum": ["asc", "desc"]},
            "created_after": _TIMESTAMP,
            "updated_after": _TIMESTAMP,
            "lookback_hours": {
                "type": "integer",
                "minimum": 1,
                "maximum": 8760,
            },
            "max_items": {"type": "integer", "minimum": 1, "maximum": 2000},
            "continuation": _PAGE_CONTINUATION,
        },
        ["project"],
    ),
    "gitlab_list_merge_request_commits": _schema(
        "gitlab_list_merge_request_commits",
        "List bounded, display-safe commits belonging to one merge request.",
        {
            "project": _PROJECT,
            "iid": {"type": "integer", "minimum": 1, "maximum": 2147483647},
            "max_items": {"type": "integer", "minimum": 1, "maximum": 2000},
            "continuation": _PAGE_CONTINUATION,
        },
        ["project", "iid"],
    ),
    "gitlab_list_merge_request_discussions": _schema(
        "gitlab_list_merge_request_discussions",
        "List bounded merge-request discussion threads, resolution state, and diff positions.",
        {
            "project": _PROJECT,
            "iid": {"type": "integer", "minimum": 1, "maximum": 2147483647},
            "max_discussions": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000,
            },
            "max_notes_per_discussion": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
            },
            "continuation": _PAGE_CONTINUATION,
        },
        ["project", "iid"],
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
    now = client_options.pop("now", None)
    return GitLabOperations(
        GitLabClient(authentication, **client_options),
        **({"now": now} if now is not None else {}),
    )


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
        if name == "gitlab_list_group_projects":
            return operations.list_group_projects(
                values["group"],
                recursive=values.get("recursive", True),
                include_shared=values.get("include_shared", False),
                include_archived=values.get("include_archived", False),
                search=values.get("search"),
                max_groups=values.get("max_groups", 200),
                max_projects=values.get("max_projects", 500),
                continuation=values.get("continuation"),
            )
        if name == "gitlab_list_commits":
            return operations.list_commits(
                values["project"],
                ref=values.get("ref"),
                path=values.get("path"),
                since=values.get("since"),
                until=values.get("until"),
                lookback_hours=values.get("lookback_hours"),
                max_items=values.get("max_items", 100),
                continuation=values.get("continuation"),
            )
        if name == "gitlab_read_commit":
            return operations.read_commit(values["project"], values["commit"])
        if name == "gitlab_list_commit_comments":
            return operations.list_commit_comments(
                values["project"],
                values["commit"],
                max_items=values.get("max_items", 100),
                continuation=values.get("continuation"),
            )
        if name == "gitlab_list_commit_discussions":
            return operations.list_commit_discussions(
                values["project"],
                values["commit"],
                max_discussions=values.get("max_discussions", 100),
                max_notes_per_discussion=values.get(
                    "max_notes_per_discussion", 100
                ),
                continuation=values.get("continuation"),
            )
        if name == "gitlab_list_merge_requests":
            return operations.list_merge_requests(
                values["project"],
                state=values.get("state", "opened"),
                source_branch=values.get("source_branch"),
                target_branch=values.get("target_branch"),
                search=values.get("search"),
                order_by=values.get("order_by", "created_at"),
                sort=values.get("sort", "desc"),
                created_after=values.get("created_after"),
                updated_after=values.get("updated_after"),
                lookback_hours=values.get("lookback_hours"),
                max_items=values.get("max_items", 100),
                continuation=values.get("continuation"),
            )
        if name == "gitlab_list_merge_request_commits":
            return operations.list_merge_request_commits(
                values["project"],
                values["iid"],
                max_items=values.get("max_items", 100),
                continuation=values.get("continuation"),
            )
        if name == "gitlab_list_merge_request_discussions":
            return operations.list_merge_request_discussions(
                values["project"],
                values["iid"],
                max_discussions=values.get("max_discussions", 100),
                max_notes_per_discussion=values.get(
                    "max_notes_per_discussion", 100
                ),
                continuation=values.get("continuation"),
            )
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
