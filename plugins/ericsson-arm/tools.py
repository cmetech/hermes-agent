"""Tool schemas and safe invocation adapters for bounded Artifactory access."""

from __future__ import annotations

from typing import Any, Mapping

if __package__:
    from .auth import authentication_from_configuration
    from .client import ArmClient
    from .models import ArmError
    from .operations import ArmOperations
else:
    from auth import authentication_from_configuration
    from client import ArmClient
    from models import ArmError
    from operations import ArmOperations


_REPO = {
    "type": "string", "minLength": 1, "maxLength": 128,
    "description": "Artifactory repository key, for example 'generic-local'.",
}
_PATH = {
    "type": "string", "maxLength": 1024,
    "description": "Path inside the repository, with no '..' segments.",
}
_LIMIT = {"type": "integer", "minimum": 1, "maximum": 100}


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
    "arm_list_repositories": _schema(
        "arm_list_repositories",
        "List visible Artifactory repositories, optionally filtered by "
        "repository type (local, remote, virtual) or package type (generic, "
        "docker, maven).",
        {
            "repository_type": {"type": "string", "maxLength": 64},
            "package_type": {"type": "string", "maxLength": 64},
            "max_results": _LIMIT,
        },
        [],
    ),
    "arm_artifact_info": _schema(
        "arm_artifact_info",
        "Fetch metadata for one Artifactory path. A file returns size, "
        "checksums and download URI; a folder returns its children. Use this "
        "rather than downloading: the sha256 is what identifies a build "
        "artefact, and it costs no bytes.",
        {
            "repo": _REPO,
            "path": _PATH,
            "max_children": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
        ["repo", "path"],
    ),
    "arm_get_properties": _schema(
        "arm_get_properties",
        "Read one artefact's Artifactory properties. CI stamps build.number, "
        "build.name and vcs.revision here, so this is how you connect a "
        "deployed artefact back to the pipeline and commit that built it.",
        {
            "repo": _REPO,
            "path": _PATH,
            "keys": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 255},
                "minItems": 1,
                "maxItems": 64,
            },
        },
        ["repo", "path"],
    ),
    "arm_search_artifacts": _schema(
        "arm_search_artifacts",
        "Search Artifactory with AQL, for example "
        "'items.find({\"repo\":\"generic-local\",\"path\":{\"$match\":"
        "\"Infra/images*\"}})'. Do not add .limit() — use max_results. "
        "Required permission fields are added automatically.",
        {
            "query": {"type": "string", "minLength": 1, "maxLength": 8192},
            "max_results": _LIMIT,
        },
        ["query"],
    ),
    "arm_deploy": _schema(
        "arm_deploy",
        "Upload one local file to Artifactory. It first tries a checksum-only "
        "deploy, then uploads the file when Artifactory does not already hold "
        "the blob. Requires dry_run or confirm.",
        {
            "repo": _REPO,
            "path": _PATH,
            "source_file": {
                "type": "string", "minLength": 1, "maxLength": 4096,
                "description": "Absolute path to the local file to upload.",
            },
            "dry_run": {"type": "boolean"},
            "confirm": {"type": "boolean"},
        },
        ["repo", "path", "source_file"],
    ),
    "arm_delete": _schema(
        "arm_delete",
        "Delete one Artifactory path. A folder path removes the whole "
        "subtree in one atomic call. Run with dry_run first to see what "
        "would be removed. Requires dry_run or confirm.",
        {
            "repo": _REPO,
            "path": {
                "type": "string", "minLength": 1, "maxLength": 1024,
                "description": (
                    "Path inside the repository. A folder path deletes it and "
                    "everything under it."
                ),
            },
            "dry_run": {"type": "boolean"},
            "confirm": {"type": "boolean"},
        },
        ["repo", "path"],
    ),
}


def check_available(configuration=None) -> bool:
    if configuration is None:
        return False
    try:
        authentication_from_configuration(configuration)
        return True
    except ArmError:
        return False


def operations_from_configuration(configuration, **client_options) -> ArmOperations:
    authentication = authentication_from_configuration(configuration)
    return ArmOperations(ArmClient(authentication, **client_options))


def invoke(name: str, args: Mapping[str, Any], configuration, **client_options):
    if name not in SCHEMAS or not isinstance(args, Mapping):
        raise ArmError("invalid_input")
    parameters = SCHEMAS[name]["parameters"]
    allowed = set(parameters["properties"])
    required = set(parameters.get("required", ()))
    if (
        any(not isinstance(key, str) for key in args)
        or not required.issubset(args)
        or not set(args).issubset(allowed)
    ):
        raise ArmError("invalid_input")
    operations = operations_from_configuration(configuration, **client_options)
    values = dict(args)
    try:
        if name == "arm_list_repositories":
            return operations.list_repositories(
                repository_type=values.get("repository_type"),
                package_type=values.get("package_type"),
                max_results=values.get(
                    "max_results", operations.client.auth.default_max_results
                ),
            )
        if name == "arm_artifact_info":
            return operations.artifact_info(
                values["repo"],
                values["path"],
                max_children=values.get("max_children", 100),
            )
        if name == "arm_get_properties":
            return operations.get_properties(
                values["repo"], values["path"], keys=values.get("keys")
            )
        if name == "arm_search_artifacts":
            return operations.search_artifacts(
                values["query"],
                max_results=values.get(
                    "max_results", operations.client.auth.default_max_results
                ),
            )
        if name == "arm_deploy":
            return operations.deploy(
                values["repo"],
                values["path"],
                values["source_file"],
                dry_run=values.get("dry_run", False),
                confirm=values.get("confirm", False),
            )
        if name == "arm_delete":
            return operations.delete(
                values["repo"],
                values["path"],
                dry_run=values.get("dry_run", False),
                confirm=values.get("confirm", False),
            )
        raise ArmError("invalid_input")
    finally:
        operations.client.close()
