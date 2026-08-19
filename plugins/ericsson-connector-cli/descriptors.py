"""Curated, network-free Ericsson connector CLI command descriptors."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class SchemaContract:
    """Immutable recursive projection of one live JSON Schema property."""

    types: tuple[str, ...] = ()
    description: str | None = None
    has_default: bool = False
    default: str | int | float | bool | None = None
    enum: tuple[str | int | float | bool | None, ...] = ()
    one_of: tuple[SchemaContract, ...] = ()
    any_of: tuple[SchemaContract, ...] = ()
    properties: tuple[tuple[str, SchemaContract], ...] = ()
    required: tuple[str, ...] = ()
    additional_properties: bool | SchemaContract | None = None
    items: SchemaContract | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None
    min_items: int | None = None
    max_items: int | None = None
    min_properties: int | None = None
    max_properties: int | None = None


@dataclass(frozen=True)
class ArgumentBinding:
    """Bind one public CLI argument to one canonical connector property."""

    source: str
    public_name: str
    target_schema_property: str
    required: bool
    repeatable: bool
    value_type: str
    choices: tuple[str, ...] = ()
    mutually_exclusive_group: str | None = None
    mutually_exclusive_group_required: bool = False
    minimum: int | None = None
    maximum: int | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None
    min_items: int | None = None
    max_items: int | None = None
    min_properties: int | None = None
    max_properties: int | None = None
    item_minimum: int | None = None
    item_maximum: int | None = None
    item_min_length: int | None = None
    item_max_length: int | None = None
    item_pattern: str | None = None
    schema_contract: SchemaContract | None = None


@dataclass(frozen=True)
class CommandDescriptor:
    """Describe one curated leaf command without importing its connector."""

    connector_id: str
    path_tokens: tuple[str, ...]
    operation: str
    access: str
    positional_bindings: tuple[ArgumentBinding, ...]
    option_bindings: tuple[ArgumentBinding, ...]
    file_bindings: tuple[ArgumentBinding, ...]
    render_hint: str


def _pos(
    public_name: str,
    target: str | None = None,
    *,
    required: bool = True,
    repeatable: bool = False,
    value_type: str = "string",
    choices: tuple[str, ...] = (),
) -> ArgumentBinding:
    return ArgumentBinding(
        "positional",
        public_name,
        target or public_name.replace("-", "_"),
        required,
        repeatable,
        value_type,
        choices,
    )


def _opt(
    public_name: str,
    target: str | None = None,
    *,
    required: bool = False,
    repeatable: bool = False,
    value_type: str = "string",
    choices: tuple[str, ...] = (),
    mutually_exclusive_group: str | None = None,
    mutually_exclusive_group_required: bool = False,
) -> ArgumentBinding:
    return ArgumentBinding(
        "option",
        f"--{public_name}",
        target or public_name.replace("-", "_"),
        required,
        repeatable,
        value_type,
        choices,
        mutually_exclusive_group,
        mutually_exclusive_group_required,
    )


def _file(
    public_name: str,
    target: str,
    *,
    required: bool,
    repeatable: bool = False,
    value_type: str = "text",
    source: str = "body_file",
    mutually_exclusive_group: str | None = None,
    mutually_exclusive_group_required: bool = False,
) -> ArgumentBinding:
    return ArgumentBinding(
        source,
        f"--{public_name}",
        target,
        required,
        repeatable,
        value_type,
        mutually_exclusive_group=mutually_exclusive_group,
        mutually_exclusive_group_required=mutually_exclusive_group_required,
    )


def _command(
    connector_id: str,
    path: tuple[str, ...],
    operation: str,
    access: str,
    *,
    positionals: tuple[ArgumentBinding, ...] = (),
    options: tuple[ArgumentBinding, ...] = (),
    files: tuple[ArgumentBinding, ...] = (),
    render_hint: str = "detail",
) -> CommandDescriptor:
    def validated(bindings: tuple[ArgumentBinding, ...]) -> tuple[ArgumentBinding, ...]:
        operation_validation = _VALIDATION.get(operation, {})
        result = []
        for binding in bindings:
            bounded = replace(
                binding,
                **operation_validation.get(binding.target_schema_property, {}),
            )
            result.append(
                replace(
                    bounded,
                    schema_contract=_schema_contract(operation, bounded),
                )
            )
        return tuple(result)

    return CommandDescriptor(
        connector_id,
        path,
        operation,
        access,
        validated(positionals),
        validated(options),
        validated(files),
        render_hint,
    )


_JIRA = "ericsson-jira"
_GITLAB = "ericsson-gitlab"
_CONFLUENCE = "ericsson-confluence"
_ARM = "ericsson-arm"
_CONTINUATION = _opt(
    "continuation", "continuation", value_type="continuation"
)


def _v(
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    min_length: int | None = None,
    max_length: int | None = None,
    pattern: str | None = None,
    min_items: int | None = None,
    max_items: int | None = None,
    min_properties: int | None = None,
    max_properties: int | None = None,
    item_minimum: int | None = None,
    item_maximum: int | None = None,
    item_min_length: int | None = None,
    item_max_length: int | None = None,
    item_pattern: str | None = None,
) -> dict[str, int | str]:
    values = locals()
    return {name: value for name, value in values.items() if value is not None}


_GITLAB_PROJECT = _v(minimum=1, min_length=1, max_length=2048)
_GITLAB_PROJECT_OPERATIONS = (
    "gitlab_resolve_project",
    "gitlab_list_commits",
    "gitlab_read_commit",
    "gitlab_list_commit_comments",
    "gitlab_list_commit_discussions",
    "gitlab_list_merge_requests",
    "gitlab_list_merge_request_commits",
    "gitlab_list_merge_request_discussions",
    "gitlab_list_repository_tree",
    "gitlab_read_file",
    "gitlab_read_merge_request",
    "gitlab_list_pipelines",
    "gitlab_read_pipeline",
    "gitlab_inspect_ci",
    "gitlab_job_log",
    "gitlab_retry_job",
    "gitlab_play_job",
    "gitlab_retry_pipeline",
    "gitlab_create_branch",
    "gitlab_create_named_branch",
    "gitlab_commit_changes",
    "gitlab_create_merge_request",
    "gitlab_create_mr_note",
    "gitlab_reply_to_discussion",
    "gitlab_resolve_discussion",
    "gitlab_merge_request_approvals",
    "gitlab_approve_merge_request",
    "gitlab_merge_merge_request",
    "gitlab_update_merge_request",
)
_VALIDATION: dict[str, dict[str, dict[str, int | str]]] = {
    operation: {"project": _GITLAB_PROJECT}
    for operation in _GITLAB_PROJECT_OPERATIONS
}


def _add_validation(
    additions: dict[str, dict[str, dict[str, int | str]]],
) -> None:
    for operation, properties in additions.items():
        _VALIDATION.setdefault(operation, {}).update(properties)


_add_validation(
    {
        "jira_my_tickets": {
            "max_results": _v(minimum=1, maximum=100),
        },
        "jira_search_issues": {
            "jql": _v(min_length=1, max_length=4096),
            "max_results": _v(minimum=1, maximum=100),
            "fields": _v(min_items=1, max_items=9),
            "statuses": _v(
                max_items=20, item_min_length=1, item_max_length=128
            ),
            "issue_types": _v(
                max_items=20, item_min_length=1, item_max_length=128
            ),
            "priorities": _v(
                max_items=20, item_min_length=1, item_max_length=128
            ),
            "labels": _v(
                max_items=20, item_min_length=1, item_max_length=128
            ),
            "min_age_days": _v(minimum=0, maximum=3650),
            "max_age_days": _v(minimum=0, maximum=3650),
        },
        "jira_get_issue": {
            "key": _v(min_length=3, max_length=128),
        },
        "jira_add_comment": {
            "key": _v(min_length=3, max_length=128),
            "body": _v(min_length=1, max_length=32000),
        },
        "jira_list_fields": {
            "max_results": _v(minimum=1, maximum=200),
        },
        "jira_get_project": {
            "key": _v(min_length=1, max_length=64),
        },
        "jira_list_transitions": {
            "key": _v(min_length=3, max_length=128),
        },
        "jira_search_assignable_users": {
            "project": _v(min_length=1, max_length=64),
            "query": _v(max_length=255),
            "max_results": _v(minimum=1, maximum=100),
        },
        "jira_transition_issue": {
            "key": _v(min_length=3, max_length=128),
            "transition_id": _v(pattern=r"^[0-9]{1,19}$"),
            "expected_status": _v(max_length=255),
        },
        "jira_assign_issue": {
            "key": _v(min_length=3, max_length=128),
            "assignee": _v(max_length=255),
        },
        "jira_update_fields": {
            "key": _v(min_length=3, max_length=128),
            "fields": _v(min_properties=1, max_properties=20),
        },
        "jira_manage_labels": {
            "key": _v(min_length=3, max_length=128),
            "labels": _v(
                min_items=1,
                max_items=50,
                item_min_length=1,
                item_max_length=255,
            ),
        },
        "jira_create_issue": {
            "project": _v(min_length=1, max_length=64),
            "issue_type": _v(min_length=1, max_length=255),
            "summary": _v(min_length=1, max_length=255),
            "description": _v(max_length=32000),
        },
        "jira_link_issues": {
            "inward": _v(min_length=3, max_length=128),
            "outward": _v(min_length=3, max_length=128),
            "link_type": _v(min_length=1, max_length=255),
        },
        "gitlab_list_group_projects": {
            "group": _GITLAB_PROJECT,
            "search": _v(min_length=1, max_length=512),
            "max_groups": _v(minimum=1, maximum=2000),
            "max_projects": _v(minimum=1, maximum=5000),
        },
        "gitlab_list_commits": {
            "ref": _v(min_length=1, max_length=512),
            "path": _v(min_length=1, max_length=4096),
            "since": _v(min_length=1, max_length=128),
            "until": _v(min_length=1, max_length=128),
            "lookback_hours": _v(minimum=1, maximum=8760),
            "max_items": _v(minimum=1, maximum=2000),
        },
        "gitlab_read_commit": {
            "commit": _v(min_length=1, max_length=512),
        },
        "gitlab_list_commit_comments": {
            "commit": _v(min_length=1, max_length=512),
            "max_items": _v(minimum=1, maximum=2000),
        },
        "gitlab_list_commit_discussions": {
            "commit": _v(min_length=1, max_length=512),
            "max_discussions": _v(minimum=1, maximum=1000),
            "max_notes_per_discussion": _v(minimum=1, maximum=500),
        },
        "gitlab_list_merge_requests": {
            "source_branch": _v(min_length=1, max_length=512),
            "target_branch": _v(min_length=1, max_length=512),
            "search": _v(min_length=1, max_length=512),
            "created_after": _v(min_length=1, max_length=128),
            "updated_after": _v(min_length=1, max_length=128),
            "lookback_hours": _v(minimum=1, maximum=8760),
            "max_items": _v(minimum=1, maximum=2000),
        },
        "gitlab_list_merge_request_commits": {
            "iid": _v(minimum=1, maximum=2147483647),
            "max_items": _v(minimum=1, maximum=2000),
        },
        "gitlab_list_merge_request_discussions": {
            "iid": _v(minimum=1, maximum=2147483647),
            "max_discussions": _v(minimum=1, maximum=1000),
            "max_notes_per_discussion": _v(minimum=1, maximum=500),
        },
        "gitlab_list_repository_tree": {
            "ref": _v(min_length=1, max_length=512),
            "path": _v(max_length=4096),
            "max_items": _v(minimum=1, maximum=2000),
        },
        "gitlab_read_file": {
            "file_path": _v(min_length=1, max_length=4096),
            "ref": _v(min_length=1, max_length=512),
            "max_bytes": _v(minimum=1, maximum=524288),
        },
        "gitlab_read_merge_request": {
            "iid": _v(minimum=1, maximum=2147483647),
        },
        "gitlab_list_pipelines": {
            "ref": _v(min_length=1, max_length=512),
            "status": _v(min_length=1, max_length=64),
            "max_items": _v(minimum=1, maximum=500),
        },
        "gitlab_read_pipeline": {
            "pipeline_id": _v(minimum=1, maximum=2147483647),
        },
        "gitlab_inspect_ci": {
            "branch_spec": _v(min_length=1, max_length=512),
            "lookback_days": _v(minimum=1, maximum=365),
            "max_branches": _v(minimum=1, maximum=100),
            "max_pages": _v(minimum=1, maximum=10),
            "max_includes": _v(minimum=1, maximum=100),
            "max_include_bytes": _v(minimum=1, maximum=524288),
            "max_groups": _v(minimum=1, maximum=20),
            "max_variables": _v(minimum=1, maximum=2000),
        },
        "gitlab_job_log": {
            "job_id": _v(minimum=1),
            "max_bytes": _v(minimum=1, maximum=200000),
        },
        "gitlab_retry_job": {"job_id": _v(minimum=1)},
        "gitlab_play_job": {"job_id": _v(minimum=1)},
        "gitlab_retry_pipeline": {"pipeline_id": _v(minimum=1)},
        "gitlab_create_branch": {
            "prefix": _v(min_length=1, max_length=512),
            "ticket_key": _v(min_length=1, max_length=128),
            "summary": _v(min_length=1, max_length=2048),
            "source_ref": _v(min_length=1, max_length=512),
        },
        "gitlab_create_named_branch": {
            "branch": _v(min_length=1, max_length=512),
            "ref": _v(min_length=1, max_length=512),
        },
        "gitlab_commit_changes": {
            "branch": _v(min_length=1, max_length=512),
            "commit_message": _v(min_length=1, max_length=4096),
            "actions": _v(min_items=1, max_items=100),
        },
        "gitlab_create_merge_request": {
            "source_branch": _v(min_length=1, max_length=512),
            "target_branch": _v(min_length=1, max_length=512),
            "title": _v(min_length=1, max_length=1024),
            "description": _v(max_length=65536),
        },
        "gitlab_create_mr_note": {
            "iid": _v(minimum=1),
            "body": _v(min_length=1, max_length=100000),
        },
        "gitlab_reply_to_discussion": {
            "iid": _v(minimum=1),
            "discussion_id": _v(min_length=1, max_length=128),
            "body": _v(min_length=1, max_length=100000),
        },
        "gitlab_resolve_discussion": {
            "iid": _v(minimum=1),
            "discussion_id": _v(min_length=1, max_length=128),
        },
        "gitlab_merge_request_approvals": {"iid": _v(minimum=1)},
        "gitlab_approve_merge_request": {
            "iid": _v(minimum=1),
            "sha": _v(pattern=r"^[0-9a-f]{7,40}$"),
        },
        "gitlab_merge_merge_request": {
            "iid": _v(minimum=1),
            "sha": _v(pattern=r"^[0-9a-f]{7,40}$"),
        },
        "gitlab_update_merge_request": {
            "iid": _v(minimum=1),
            "title": _v(min_length=1, max_length=1024),
            "description": _v(max_length=65536),
            "add_labels": _v(
                min_items=1,
                max_items=50,
                item_min_length=1,
                item_max_length=255,
            ),
            "remove_labels": _v(
                min_items=1,
                max_items=50,
                item_min_length=1,
                item_max_length=255,
            ),
        },
        "confluence_get_page": {
            "content_id": _v(pattern=r"^[0-9]{1,19}$"),
        },
        "confluence_get_page_body": {
            "content_id": _v(pattern=r"^[0-9]{1,19}$"),
            "max_chars": _v(minimum=1, maximum=100000),
        },
        "confluence_search": {
            "cql": _v(min_length=1, max_length=4096),
            "max_results": _v(minimum=1, maximum=100),
        },
        "confluence_list_spaces": {
            "max_results": _v(minimum=1, maximum=100),
        },
        "confluence_list_children": {
            "content_id": _v(pattern=r"^[0-9]{1,19}$"),
            "max_results": _v(minimum=1, maximum=100),
        },
        "confluence_list_comments": {
            "content_id": _v(pattern=r"^[0-9]{1,19}$"),
            "max_results": _v(minimum=1, maximum=100),
        },
        "confluence_create_page": {
            "space_key": _v(min_length=1, max_length=255),
            "title": _v(min_length=1, max_length=255),
            "markdown": _v(max_length=65536),
            "parent_id": _v(pattern=r"^[0-9]{1,19}$"),
        },
        "confluence_update_page": {
            "content_id": _v(pattern=r"^[0-9]{1,19}$"),
            "title": _v(min_length=1, max_length=255),
            "markdown": _v(max_length=65536),
        },
        "confluence_add_comment": {
            "content_id": _v(pattern=r"^[0-9]{1,19}$"),
            "markdown": _v(min_length=1, max_length=65536),
        },
        "arm_list_repositories": {
            "repository_type": _v(max_length=64),
            "package_type": _v(max_length=64),
            "max_results": _v(minimum=1, maximum=100),
        },
        "arm_artifact_info": {
            "repo": _v(min_length=1, max_length=128),
            "path": _v(max_length=1024),
            "max_children": _v(minimum=1, maximum=1000),
        },
        "arm_get_properties": {
            "repo": _v(min_length=1, max_length=128),
            "path": _v(max_length=1024),
            "keys": _v(
                min_items=1,
                max_items=64,
                item_min_length=1,
                item_max_length=255,
            ),
        },
        "arm_search_artifacts": {
            "query": _v(min_length=1, max_length=8192),
            "max_results": _v(minimum=1, maximum=100),
        },
        "arm_deploy": {
            "repo": _v(min_length=1, max_length=128),
            "path": _v(max_length=1024),
            "source_file": _v(min_length=1, max_length=4096),
        },
        "arm_delete": {
            "repo": _v(min_length=1, max_length=128),
            "path": _v(min_length=1, max_length=1024),
        },
    }
)


_PROJECT_DESCRIPTION = (
    "Project numeric id, namespace/project slug, or URL on the configured "
    "GitLab origin."
)
_SCHEMA_DESCRIPTIONS = {
    **{
        (operation, "project"): _PROJECT_DESCRIPTION
        for operation in _GITLAB_PROJECT_OPERATIONS
    },
    (
        "gitlab_list_group_projects",
        "group",
    ): "Group numeric id, full nested group path, or group URL on the configured GitLab origin.",
    ("gitlab_create_branch", "prefix"): "Branch prefix; defaults to fix.",
    (
        "arm_artifact_info",
        "repo",
    ): "Artifactory repository key, for example 'generic-local'.",
    (
        "arm_artifact_info",
        "path",
    ): "Path inside the repository, with no '..' segments.",
    (
        "arm_get_properties",
        "repo",
    ): "Artifactory repository key, for example 'generic-local'.",
    (
        "arm_get_properties",
        "path",
    ): "Path inside the repository, with no '..' segments.",
    (
        "arm_deploy",
        "repo",
    ): "Artifactory repository key, for example 'generic-local'.",
    ("arm_deploy", "path"): "Path inside the repository, with no '..' segments.",
    ("arm_deploy", "source_file"): "Absolute path to the local file to upload.",
    (
        "arm_delete",
        "repo",
    ): "Artifactory repository key, for example 'generic-local'.",
    (
        "arm_delete",
        "path",
    ): "Path inside the repository. A folder path deletes it and everything under it.",
}
_SCHEMA_DEFAULTS = {
    ("jira_list_fields", "custom_only"): False,
    ("gitlab_create_branch", "prefix"): "fix",
}


def _object_contract(
    properties: dict[str, SchemaContract],
    *,
    required: tuple[str, ...] = (),
    additional_properties: bool | None = False,
    min_properties: int | None = None,
    max_properties: int | None = None,
) -> SchemaContract:
    return SchemaContract(
        types=("object",),
        properties=tuple(sorted(properties.items())),
        required=required,
        additional_properties=additional_properties,
        min_properties=min_properties,
        max_properties=max_properties,
    )


def _page_contract() -> SchemaContract:
    return _object_contract(
        {
            "next_page": SchemaContract(types=("integer",), minimum=1),
            "offset": SchemaContract(
                types=("integer",), minimum=0, maximum=99
            ),
            "page": SchemaContract(types=("integer",), minimum=1),
        }
    )


_CONTINUATION_SCHEMA = _page_contract()
_GROUP_CONTINUATION_SCHEMA = _object_contract(
    {
        "groups": _page_contract(),
        "projects": _page_contract(),
    }
)
_CHANGE_ACTIONS_SCHEMA = SchemaContract(
    types=("array",),
    min_items=1,
    max_items=100,
    items=_object_contract(
        {
            "action": SchemaContract(
                types=("string",), enum=("create", "update", "delete")
            ),
            "content": SchemaContract(types=("string",), max_length=524288),
            "file_path": SchemaContract(
                types=("string",), min_length=1, max_length=4096
            ),
            "last_commit_id": SchemaContract(
                types=("string",), min_length=1, max_length=512
            ),
        },
        required=("action", "file_path"),
    ),
)


def _schema_contract(operation: str, binding: ArgumentBinding) -> SchemaContract:
    """Build the complete frozen property schema owned by one binding."""
    identity = (operation, binding.target_schema_property)
    description = _SCHEMA_DESCRIPTIONS.get(identity)
    has_default = identity in _SCHEMA_DEFAULTS
    default = _SCHEMA_DEFAULTS.get(identity)

    if binding.value_type in {
        "continuation",
        "group_continuation",
        "project_continuation",
    }:
        if operation == "gitlab_list_group_projects":
            return _GROUP_CONTINUATION_SCHEMA
        return _CONTINUATION_SCHEMA
    if binding.value_type == "change_object_file":
        return _CHANGE_ACTIONS_SCHEMA
    if binding.value_type == "field_assignment":
        return _object_contract(
            {},
            additional_properties=None,
            min_properties=binding.min_properties,
            max_properties=binding.max_properties,
        )
    if binding.value_type == "string_or_integer":
        return SchemaContract(
            description=description,
            one_of=(
                SchemaContract(
                    types=("string",),
                    min_length=binding.min_length,
                    max_length=binding.max_length,
                ),
                SchemaContract(
                    types=("integer",),
                    minimum=binding.minimum,
                    maximum=binding.maximum,
                ),
            ),
        )
    if binding.repeatable:
        return SchemaContract(
            types=("array",),
            items=SchemaContract(
                types=("string",),
                enum=binding.choices,
                minimum=binding.item_minimum,
                maximum=binding.item_maximum,
                min_length=binding.item_min_length,
                max_length=binding.item_max_length,
                pattern=binding.item_pattern,
            ),
            min_items=binding.min_items,
            max_items=binding.max_items,
        )

    types = {
        "boolean": ("boolean",),
        "integer": ("integer",),
        "nullable_string": ("string", "null"),
        "path": ("string",),
        "string": ("string",),
        "text": ("string",),
    }[binding.value_type]
    return SchemaContract(
        types=types,
        description=description,
        has_default=has_default,
        default=default,
        enum=binding.choices,
        minimum=binding.minimum,
        maximum=binding.maximum,
        min_length=binding.min_length,
        max_length=binding.max_length,
        pattern=binding.pattern,
        min_items=binding.min_items,
        max_items=binding.max_items,
        min_properties=binding.min_properties,
        max_properties=binding.max_properties,
    )


DESCRIPTORS: tuple[CommandDescriptor, ...] = (
    _command(
        _JIRA,
        ("jira", "issue", "mine"),
        "jira_my_tickets",
        "read",
        options=(_opt("max-results", value_type="integer"),),
        render_hint="issue-list",
    ),
    _command(
        _JIRA,
        ("jira", "issue", "search"),
        "jira_search_issues",
        "read",
        options=(
            _opt("jql", required=True),
            _opt("max-results", required=True, value_type="integer"),
            _opt(
                "field",
                "fields",
                repeatable=True,
                choices=(
                    "created",
                    "description",
                    "environment",
                    "issuetype",
                    "labels",
                    "priority",
                    "status",
                    "summary",
                    "updated",
                ),
            ),
            _opt("status", "statuses", repeatable=True),
            _opt("issue-type", "issue_types", repeatable=True),
            _opt("priority", "priorities", repeatable=True),
            _opt("label", "labels", repeatable=True),
            _opt("min-age-days", value_type="integer"),
            _opt("max-age-days", value_type="integer"),
        ),
        render_hint="issue-list",
    ),
    _command(
        _JIRA,
        ("jira", "issue", "get"),
        "jira_get_issue",
        "read",
        positionals=(_pos("key"),),
        render_hint="issue-detail",
    ),
    _command(
        _JIRA,
        ("jira", "issue", "comment"),
        "jira_add_comment",
        "write",
        positionals=(_pos("key"),),
        files=(_file("body-file", "body", required=True),),
        render_hint="write-result",
    ),
    _command(
        _JIRA,
        ("jira", "field", "list"),
        "jira_list_fields",
        "read",
        options=(
            _opt("custom-only", value_type="boolean"),
            _opt("max-results", value_type="integer"),
        ),
        render_hint="field-list",
    ),
    _command(
        _JIRA,
        ("jira", "project", "get"),
        "jira_get_project",
        "read",
        positionals=(_pos("project", "key"),),
        render_hint="project-detail",
    ),
    _command(
        _JIRA,
        ("jira", "transition", "list"),
        "jira_list_transitions",
        "read",
        positionals=(_pos("key"),),
        render_hint="transition-list",
    ),
    _command(
        _JIRA,
        ("jira", "user", "search-assignable"),
        "jira_search_assignable_users",
        "read",
        positionals=(_pos("project"),),
        options=(
            _opt("query"),
            _opt("max-results", value_type="integer"),
        ),
        render_hint="user-list",
    ),
    _command(
        _JIRA,
        ("jira", "issue", "transition"),
        "jira_transition_issue",
        "write",
        positionals=(
            _pos("key"),
            _pos("transition-id"),
        ),
        options=(_opt("expected-status"),),
        render_hint="write-result",
    ),
    _command(
        _JIRA,
        ("jira", "issue", "assign"),
        "jira_assign_issue",
        "write",
        positionals=(
            _pos("key"),
            _pos("assignee", value_type="nullable_string"),
        ),
        render_hint="write-result",
    ),
    _command(
        _JIRA,
        ("jira", "issue", "update"),
        "jira_update_fields",
        "write",
        positionals=(_pos("key"),),
        options=(
            _opt(
                "field",
                "fields",
                required=True,
                repeatable=True,
                value_type="field_assignment",
            ),
        ),
        render_hint="write-result",
    ),
    _command(
        _JIRA,
        ("jira", "issue", "label"),
        "jira_manage_labels",
        "write",
        positionals=(
            _pos("key"),
            _pos("operation", choices=("add", "remove")),
            _pos("label", "labels", repeatable=True),
        ),
        render_hint="write-result",
    ),
    _command(
        _JIRA,
        ("jira", "issue", "create"),
        "jira_create_issue",
        "write",
        positionals=(
            _pos("project"),
            _pos("issue-type"),
        ),
        options=(
            _opt("summary", required=True),
            _opt("description"),
        ),
        render_hint="write-result",
    ),
    _command(
        _JIRA,
        ("jira", "link-type", "list"),
        "jira_list_link_types",
        "read",
        render_hint="link-type-list",
    ),
    _command(
        _JIRA,
        ("jira", "issue", "link"),
        "jira_link_issues",
        "write",
        positionals=(
            _pos("inward-key", "inward"),
            _pos("outward-key", "outward"),
            _pos("link-type"),
        ),
        render_hint="write-result",
    ),
    _command(
        _GITLAB,
        ("gitlab", "project", "resolve"),
        "gitlab_resolve_project",
        "read",
        positionals=(_pos("project", value_type="string_or_integer"),),
        render_hint="project-detail",
    ),
    _command(
        _GITLAB,
        ("gitlab", "group", "project-list"),
        "gitlab_list_group_projects",
        "read",
        positionals=(_pos("group", value_type="string_or_integer"),),
        options=(
            _opt("recursive", value_type="boolean"),
            _opt("include-shared", value_type="boolean"),
            _opt("include-archived", value_type="boolean"),
            _opt("search"),
            _opt("max-groups", value_type="integer"),
            _opt("max-projects", value_type="integer"),
            _opt(
                "group-continuation",
                "continuation",
                value_type="group_continuation",
            ),
            _opt(
                "project-continuation",
                "continuation",
                value_type="project_continuation",
            ),
        ),
        render_hint="project-list",
    ),
    _command(
        _GITLAB,
        ("gitlab", "commit", "list"),
        "gitlab_list_commits",
        "read",
        positionals=(_pos("project", value_type="string_or_integer"),),
        options=(
            _opt("ref"),
            _opt("path"),
            _opt("since"),
            _opt("until"),
            _opt("lookback-hours", value_type="integer"),
            _opt("max-items", value_type="integer"),
            _CONTINUATION,
        ),
        render_hint="commit-list",
    ),
    _command(
        _GITLAB,
        ("gitlab", "commit", "show"),
        "gitlab_read_commit",
        "read",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("sha", "commit"),
        ),
        render_hint="commit-detail",
    ),
    _command(
        _GITLAB,
        ("gitlab", "commit", "comment-list"),
        "gitlab_list_commit_comments",
        "read",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("sha", "commit"),
        ),
        options=(
            _opt("max-items", value_type="integer"),
            _CONTINUATION,
        ),
        render_hint="comment-list",
    ),
    _command(
        _GITLAB,
        ("gitlab", "commit", "discussion-list"),
        "gitlab_list_commit_discussions",
        "read",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("sha", "commit"),
        ),
        options=(
            _opt("max-discussions", value_type="integer"),
            _opt("max-notes-per-discussion", value_type="integer"),
            _CONTINUATION,
        ),
        render_hint="discussion-list",
    ),
    _command(
        _GITLAB,
        ("gitlab", "mr", "list"),
        "gitlab_list_merge_requests",
        "read",
        positionals=(_pos("project", value_type="string_or_integer"),),
        options=(
            _opt("state", choices=("open", "opened", "closed", "merged", "all")),
            _opt("source-branch"),
            _opt("target-branch"),
            _opt("search"),
            _opt("order-by", choices=("created_at", "updated_at")),
            _opt("sort", choices=("asc", "desc")),
            _opt("created-after"),
            _opt("updated-after"),
            _opt("lookback-hours", value_type="integer"),
            _opt("max-items", value_type="integer"),
            _CONTINUATION,
        ),
        render_hint="merge-request-list",
    ),
    _command(
        _GITLAB,
        ("gitlab", "mr", "commit-list"),
        "gitlab_list_merge_request_commits",
        "read",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("iid", value_type="integer"),
        ),
        options=(
            _opt("max-items", value_type="integer"),
            _CONTINUATION,
        ),
        render_hint="commit-list",
    ),
    _command(
        _GITLAB,
        ("gitlab", "mr", "discussion-list"),
        "gitlab_list_merge_request_discussions",
        "read",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("iid", value_type="integer"),
        ),
        options=(
            _opt("max-discussions", value_type="integer"),
            _opt("max-notes-per-discussion", value_type="integer"),
            _CONTINUATION,
        ),
        render_hint="discussion-list",
    ),
    _command(
        _GITLAB,
        ("gitlab", "repository", "tree"),
        "gitlab_list_repository_tree",
        "read",
        positionals=(_pos("project", value_type="string_or_integer"),),
        options=(
            _opt("ref", required=True),
            _opt("path"),
            _opt("recursive", value_type="boolean"),
            _opt("max-items", value_type="integer"),
        ),
        render_hint="repository-tree",
    ),
    _command(
        _GITLAB,
        ("gitlab", "file", "show"),
        "gitlab_read_file",
        "read",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("path", "file_path"),
        ),
        options=(
            _opt("ref", required=True),
            _opt("max-bytes", value_type="integer"),
        ),
        render_hint="file-detail",
    ),
    _command(
        _GITLAB,
        ("gitlab", "mr", "show"),
        "gitlab_read_merge_request",
        "read",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("iid", value_type="integer"),
        ),
        render_hint="merge-request-detail",
    ),
    _command(
        _GITLAB,
        ("gitlab", "pipeline", "list"),
        "gitlab_list_pipelines",
        "read",
        positionals=(_pos("project", value_type="string_or_integer"),),
        options=(
            _opt("ref"),
            _opt("status"),
            _opt("max-items", value_type="integer"),
        ),
        render_hint="pipeline-list",
    ),
    _command(
        _GITLAB,
        ("gitlab", "pipeline", "view"),
        "gitlab_read_pipeline",
        "read",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("pipeline-id", value_type="integer"),
        ),
        render_hint="pipeline-detail",
    ),
    _command(
        _GITLAB,
        ("gitlab", "ci", "inspect"),
        "gitlab_inspect_ci",
        "read",
        positionals=(_pos("project", value_type="string_or_integer"),),
        options=(
            _opt("branch-spec"),
            _opt("lookback-days", value_type="integer"),
            _opt("collect-variables", value_type="boolean"),
            _opt("max-branches", value_type="integer"),
            _opt("max-pages", value_type="integer"),
            _opt("max-includes", value_type="integer"),
            _opt("max-include-bytes", value_type="integer"),
            _opt("max-groups", value_type="integer"),
            _opt("max-variables", value_type="integer"),
        ),
        render_hint="ci-inspection",
    ),
    _command(
        _GITLAB,
        ("gitlab", "branch", "create"),
        "gitlab_create_named_branch",
        "write",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("branch"),
            _pos("ref"),
        ),
        render_hint="write-result",
    ),
    _command(
        _GITLAB,
        ("gitlab", "branch", "create-ticket"),
        "gitlab_create_branch",
        "write",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("ticket-key"),
        ),
        options=(
            _opt("summary", required=True),
            _opt("prefix"),
            _opt("source-ref"),
        ),
        render_hint="write-result",
    ),
    _command(
        _GITLAB,
        ("gitlab", "commit", "create"),
        "gitlab_commit_changes",
        "write",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("branch"),
        ),
        options=(_opt("commit-message", required=True),),
        files=(
            _file(
                "change-file",
                "actions",
                required=True,
                repeatable=True,
                value_type="change_object_file",
            ),
        ),
        render_hint="write-result",
    ),
    _command(
        _GITLAB,
        ("gitlab", "mr", "create"),
        "gitlab_create_merge_request",
        "write",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("source-branch"),
            _pos("target-branch"),
        ),
        options=(
            _opt("title", required=True),
            _opt("description"),
            _opt("remove-source-branch", value_type="boolean"),
            _opt("squash", value_type="boolean"),
        ),
        render_hint="write-result",
    ),
    _command(
        _GITLAB,
        ("gitlab", "job", "log"),
        "gitlab_job_log",
        "read",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("job-id", value_type="integer"),
        ),
        options=(_opt("max-bytes", value_type="integer"),),
        render_hint="job-log",
    ),
    _command(
        _GITLAB,
        ("gitlab", "mr", "note"),
        "gitlab_create_mr_note",
        "write",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("iid", value_type="integer"),
        ),
        files=(_file("body-file", "body", required=True),),
        render_hint="write-result",
    ),
    _command(
        _GITLAB,
        ("gitlab", "mr", "discussion-reply"),
        "gitlab_reply_to_discussion",
        "write",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("iid", value_type="integer"),
            _pos("discussion-id"),
        ),
        files=(_file("body-file", "body", required=True),),
        render_hint="write-result",
    ),
    _command(
        _GITLAB,
        ("gitlab", "mr", "discussion-resolve"),
        "gitlab_resolve_discussion",
        "write",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("iid", value_type="integer"),
            _pos("discussion-id"),
        ),
        options=(_opt("resolved", value_type="boolean"),),
        render_hint="write-result",
    ),
    _command(
        _GITLAB,
        ("gitlab", "mr", "approval-show"),
        "gitlab_merge_request_approvals",
        "read",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("iid", value_type="integer"),
        ),
        render_hint="approval-detail",
    ),
    _command(
        _GITLAB,
        ("gitlab", "mr", "approve"),
        "gitlab_approve_merge_request",
        "write",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("iid", value_type="integer"),
        ),
        options=(_opt("sha"),),
        render_hint="write-result",
    ),
    _command(
        _GITLAB,
        ("gitlab", "mr", "merge"),
        "gitlab_merge_merge_request",
        "write",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("iid", value_type="integer"),
        ),
        options=(
            _opt("sha"),
            _opt("squash", value_type="boolean"),
            _opt("remove-source-branch", value_type="boolean"),
            _opt("merge-when-pipeline-succeeds", value_type="boolean"),
        ),
        render_hint="write-result",
    ),
    _command(
        _GITLAB,
        ("gitlab", "mr", "update"),
        "gitlab_update_merge_request",
        "write",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("iid", value_type="integer"),
        ),
        options=(
            _opt("title"),
            _opt("description"),
            _opt("add-label", "add_labels", repeatable=True),
            _opt("remove-label", "remove_labels", repeatable=True),
            _opt("state-event", choices=("close", "reopen")),
            _opt("draft", value_type="boolean"),
        ),
        render_hint="write-result",
    ),
    _command(
        _GITLAB,
        ("gitlab", "job", "play"),
        "gitlab_play_job",
        "write",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("job-id", value_type="integer"),
        ),
        render_hint="write-result",
    ),
    _command(
        _GITLAB,
        ("gitlab", "job", "retry"),
        "gitlab_retry_job",
        "write",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("job-id", value_type="integer"),
        ),
        render_hint="write-result",
    ),
    _command(
        _GITLAB,
        ("gitlab", "pipeline", "retry"),
        "gitlab_retry_pipeline",
        "write",
        positionals=(
            _pos("project", value_type="string_or_integer"),
            _pos("pipeline-id", value_type="integer"),
        ),
        render_hint="write-result",
    ),
    _command(
        _CONFLUENCE,
        ("confluence", "space", "list"),
        "confluence_list_spaces",
        "read",
        options=(
            _opt("space-type", choices=("global", "personal")),
            _opt("max-results", value_type="integer"),
        ),
        render_hint="space-list",
    ),
    _command(
        _CONFLUENCE,
        ("confluence", "page", "search"),
        "confluence_search",
        "read",
        options=(
            _opt("cql", required=True),
            _opt("max-results", value_type="integer"),
        ),
        render_hint="page-list",
    ),
    _command(
        _CONFLUENCE,
        ("confluence", "page", "get"),
        "confluence_get_page",
        "read",
        positionals=(_pos("content-id"),),
        render_hint="page-detail",
    ),
    _command(
        _CONFLUENCE,
        ("confluence", "page", "body"),
        "confluence_get_page_body",
        "read",
        positionals=(_pos("content-id"),),
        options=(
            _opt("raw-storage", value_type="boolean"),
            _opt("max-chars", value_type="integer"),
        ),
        render_hint="page-body",
    ),
    _command(
        _CONFLUENCE,
        ("confluence", "page", "child-list"),
        "confluence_list_children",
        "read",
        positionals=(_pos("content-id"),),
        options=(_opt("max-results", value_type="integer"),),
        render_hint="page-list",
    ),
    _command(
        _CONFLUENCE,
        ("confluence", "page", "comment-list"),
        "confluence_list_comments",
        "read",
        positionals=(_pos("content-id"),),
        options=(_opt("max-results", value_type="integer"),),
        render_hint="comment-list",
    ),
    _command(
        _CONFLUENCE,
        ("confluence", "page", "create"),
        "confluence_create_page",
        "write",
        positionals=(_pos("space-key"),),
        options=(
            _opt("title", required=True),
            _opt("parent-id"),
        ),
        files=(_file("body-file", "markdown", required=True),),
        render_hint="write-result",
    ),
    _command(
        _CONFLUENCE,
        ("confluence", "page", "update"),
        "confluence_update_page",
        "write",
        positionals=(_pos("content-id"),),
        options=(_opt("title"),),
        files=(_file("body-file", "markdown", required=False),),
        render_hint="write-result",
    ),
    _command(
        _CONFLUENCE,
        ("confluence", "page", "comment"),
        "confluence_add_comment",
        "write",
        positionals=(_pos("content-id"),),
        files=(_file("body-file", "markdown", required=True),),
        render_hint="write-result",
    ),
    _command(
        _ARM,
        ("arm", "repository", "list"),
        "arm_list_repositories",
        "read",
        options=(
            _opt("repository-type"),
            _opt("package-type"),
            _opt("max-results", value_type="integer"),
        ),
        render_hint="repository-list",
    ),
    _command(
        _ARM,
        ("arm", "artifact", "info"),
        "arm_artifact_info",
        "read",
        positionals=(
            _pos("repo"),
            _pos("path"),
        ),
        options=(_opt("max-children", value_type="integer"),),
        render_hint="artifact-detail",
    ),
    _command(
        _ARM,
        ("arm", "artifact", "properties"),
        "arm_get_properties",
        "read",
        positionals=(
            _pos("repo"),
            _pos("path"),
        ),
        options=(_opt("key", "keys", repeatable=True),),
        render_hint="property-list",
    ),
    _command(
        _ARM,
        ("arm", "artifact", "search"),
        "arm_search_artifacts",
        "read",
        options=(
            _opt(
                "query",
                mutually_exclusive_group="aql-input",
                mutually_exclusive_group_required=True,
            ),
            _opt("max-results", value_type="integer"),
        ),
        files=(
            _file(
                "query-file",
                "query",
                required=False,
                mutually_exclusive_group="aql-input",
                mutually_exclusive_group_required=True,
            ),
        ),
        render_hint="artifact-list",
    ),
    _command(
        _ARM,
        ("arm", "artifact", "deploy"),
        "arm_deploy",
        "write",
        positionals=(
            _pos("repo"),
            _pos("path"),
        ),
        files=(
            _file(
                "file",
                "source_file",
                required=True,
                value_type="path",
                source="local_file",
            ),
        ),
        render_hint="write-result",
    ),
    _command(
        _ARM,
        ("arm", "artifact", "delete"),
        "arm_delete",
        "write",
        positionals=(
            _pos("repo"),
            _pos("path"),
        ),
        render_hint="write-result",
    ),
)


__all__ = [
    "ArgumentBinding",
    "CommandDescriptor",
    "SchemaContract",
    "DESCRIPTORS",
]
