"""Strict public and persisted models for workflow package marketplaces."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal
import unicodedata

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from hermes_cli.git_source import (
    GitSourceError,
    validate_credential_free_git_source,
)


SEMANTIC_VERSION_PATTERN = (
    r"^(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
SHA256_PATTERN = r"^[0-9a-f]{64}$"
PACKAGE_ID_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
SOURCE_NAME_PATTERN = r"^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$"
TAG_PATTERN = r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$"
CANONICAL_RELATIVE_PATH_PATTERN = (
    r"^(?!/)(?![A-Za-z]:)(?!.*\/[A-Za-z]:)"
    r"(?!.*(?:^|/)\.\.?(/|$))(?!.*(?:^|/)\.[gG][iI][tT](?:/|$))"
    r"(?!.*\\)(?!.*\x00).+$"
)

_SEMANTIC_VERSION = re.compile(SEMANTIC_VERSION_PATTERN, re.ASCII)
_TAG = re.compile(TAG_PATTERN, re.ASCII)
BoundedText = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=256)]
RequirementName = Annotated[str, StringConstraints(min_length=1, max_length=128)]
TagName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=TAG_PATTERN),
]


class StrictMarketplaceModel(BaseModel):
    """Base for untrusted JSON boundaries and local persisted records."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )


def _require_clean_text(value: str, *, label: str) -> str:
    if value != value.strip() or "\x00" in value:
        raise ValueError(f"{label} must be canonical non-empty text")
    return value


def _require_credential_free_repository_identity(value: str) -> str:
    value = _require_clean_text(value, label="repository identity")
    try:
        validate_credential_free_git_source(value)
    except GitSourceError as error:
        message = (
            "repository identity must not contain credentials"
            if "credentials" in str(error).casefold()
            else "repository identity must be valid"
        )
        raise ValueError(message) from error
    return value


def _require_canonical_relative_path(value: str) -> str:
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError("path must use NFC Unicode normalization")
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or "\x00" in value
        or value.endswith("/")
    ):
        raise ValueError("path must be a canonical package-relative path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("path must be a canonical package-relative path")
    if any(re.match(r"^[A-Za-z]:", part) for part in parts):
        raise ValueError("path must be a canonical package-relative path")
    if any(part.casefold() == ".git" for part in parts):
        raise ValueError("path must not contain repository metadata")
    return value


def _canonical_path_identity(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _paths_have_canonical_collision(values: list[str]) -> bool:
    identities = [_canonical_path_identity(value) for value in values]
    return len(identities) != len(set(identities))


def _paths_have_segment_ancestry(values: list[str]) -> bool:
    identities = {_canonical_path_identity(value) for value in values}
    for child in identities:
        segments = child.split("/")
        if any(
            "/".join(segments[:boundary]) in identities
            for boundary in range(1, len(segments))
        ):
            return True
    return False


def _require_unique_text(values: list[str], *, label: str) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must not contain duplicates")
    for value in values:
        _require_clean_text(value, label=label)
    return values


class WorkflowMember(StrictMarketplaceModel):
    """One declared workflow definition and its optional companion YAML."""

    definition: str = Field(
        min_length=1,
        max_length=1024,
        json_schema_extra={"pattern": CANONICAL_RELATIVE_PATH_PATTERN},
    )
    companion: str | None = Field(
        default=None,
        min_length=1,
        max_length=1024,
        json_schema_extra={"pattern": CANONICAL_RELATIVE_PATH_PATTERN},
    )

    @field_validator("definition", "companion")
    @classmethod
    def validate_yaml_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = _require_canonical_relative_path(value)
        if not value.endswith((".yaml", ".yml")):
            raise ValueError("workflow member path must identify YAML")
        return value

    @model_validator(mode="after")
    def reject_self_alias(self) -> "WorkflowMember":
        if self.companion is not None and _paths_have_canonical_collision([
            self.definition,
            self.companion,
        ]):
            raise ValueError("workflow member path must not alias another member")
        return self


class ExternalRequirements(StrictMarketplaceModel):
    """Declared destination capabilities; absence is advisory, not corruption."""

    runtimes: list[RequirementName] = Field(max_length=128)
    tools: list[RequirementName] = Field(max_length=128)
    providers: list[RequirementName] = Field(max_length=128)
    services: list[RequirementName] = Field(max_length=128)
    secrets: list[RequirementName] = Field(max_length=128)

    @field_validator("runtimes", "tools", "providers", "services", "secrets")
    @classmethod
    def validate_requirements(cls, value: list[str]) -> list[str]:
        return _require_unique_text(value, label="external requirements")


class WorkflowPackageManifest(StrictMarketplaceModel):
    """Canonical ``workflow-package.json`` version-one manifest."""

    schema_version: Literal[1] = Field(alias="schemaVersion")
    id: str = Field(min_length=1, max_length=64, pattern=PACKAGE_ID_PATTERN)
    version: str = Field(
        min_length=1,
        max_length=128,
        json_schema_extra={"pattern": SEMANTIC_VERSION_PATTERN},
    )
    display_name: ShortText = Field(alias="displayName")
    description: BoundedText
    license: ShortText
    publisher: ShortText
    tags: list[TagName] = Field(min_length=1, max_length=64)
    workflows: list[WorkflowMember] = Field(min_length=1, max_length=512)
    external_requirements: ExternalRequirements = Field(alias="externalRequirements")

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if _SEMANTIC_VERSION.fullmatch(value) is None:
            raise ValueError("version must be a semantic version")
        return value

    @field_validator("display_name", "description", "license", "publisher")
    @classmethod
    def validate_metadata(cls, value: str) -> str:
        return _require_clean_text(value, label="package metadata")

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("tags must not contain duplicates")
        if any(_TAG.fullmatch(tag) is None for tag in value):
            raise ValueError("tags must use canonical lowercase identifiers")
        return value

    @model_validator(mode="after")
    def reject_member_aliases(self) -> "WorkflowPackageManifest":
        paths = [member.definition for member in self.workflows]
        paths.extend(
            member.companion
            for member in self.workflows
            if member.companion is not None
        )
        if _paths_have_canonical_collision(paths):
            raise ValueError("workflow member path must not alias another member")
        return self


class WorkflowPackageIndexEntry(StrictMarketplaceModel):
    """Bounded discovery metadata for one repository package."""

    id: str = Field(min_length=1, max_length=64, pattern=PACKAGE_ID_PATTERN)
    version: str = Field(
        min_length=1,
        max_length=128,
        json_schema_extra={"pattern": SEMANTIC_VERSION_PATTERN},
    )
    display_name: ShortText = Field(alias="displayName")
    description: BoundedText
    license: ShortText
    publisher: ShortText
    tags: list[TagName] = Field(min_length=1, max_length=64)
    package_path: str = Field(
        alias="packagePath",
        min_length=1,
        max_length=1024,
        json_schema_extra={"pattern": CANONICAL_RELATIVE_PATH_PATTERN},
    )
    contract_version: Literal[1] = Field(alias="contractVersion")
    package_digest: str = Field(alias="packageDigest", pattern=SHA256_PATTERN)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if _SEMANTIC_VERSION.fullmatch(value) is None:
            raise ValueError("version must be a semantic version")
        return value

    @field_validator("display_name", "description", "license", "publisher")
    @classmethod
    def validate_metadata(cls, value: str) -> str:
        return _require_clean_text(value, label="package metadata")

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(
            _TAG.fullmatch(tag) is None for tag in value
        ):
            raise ValueError("tags must be unique canonical identifiers")
        return value

    @field_validator("package_path")
    @classmethod
    def validate_package_path(cls, value: str) -> str:
        return _require_canonical_relative_path(value)


class WorkflowPackageIndex(StrictMarketplaceModel):
    """Canonical repository marketplace index."""

    schema_version: Literal[1] = Field(alias="schemaVersion")
    packages: list[WorkflowPackageIndexEntry] = Field(max_length=4096)

    @model_validator(mode="after")
    def validate_order_and_identity(self) -> "WorkflowPackageIndex":
        ids = [entry.id for entry in self.packages]
        if len(ids) != len(set(ids)):
            raise ValueError("marketplace package ids must be unique")
        if ids != sorted(ids):
            raise ValueError("marketplace package entries must be sorted by id")
        paths = [entry.package_path for entry in self.packages]
        if _paths_have_canonical_collision(paths):
            raise ValueError("marketplace package paths must be unique")
        if _paths_have_segment_ancestry(paths):
            raise ValueError("marketplace package roots must not be nested")
        return self


class WorkflowPackageDigestRecord(StrictMarketplaceModel):
    """Exact-byte digest claim for one included package file."""

    path: str = Field(
        min_length=1,
        max_length=1024,
        json_schema_extra={"pattern": CANONICAL_RELATIVE_PATH_PATTERN},
    )
    size: int = Field(ge=0, le=1024 * 1024)
    sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        value = _require_canonical_relative_path(value)
        if value == "digests.json":
            raise ValueError("digests.json is excluded from its own digest records")
        return value


class WorkflowPackageDigests(StrictMarketplaceModel):
    """Canonical generated ``digests.json`` claim."""

    contract_version: Literal[1] = Field(alias="contractVersion")
    algorithm: Literal["sha256"]
    files: list[WorkflowPackageDigestRecord] = Field(min_length=1, max_length=512)
    package_digest: str = Field(alias="packageDigest", pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_order_and_identity(self) -> "WorkflowPackageDigests":
        paths = [record.path for record in self.files]
        if _paths_have_canonical_collision(paths):
            raise ValueError("digest record paths must be unique")
        if paths != sorted(paths):
            raise ValueError("digest records must be sorted by path")
        return self


class PathRules(StrictMarketplaceModel):
    separator: Literal["/"]
    relative_only: Literal[True]
    unicode_normalization: Literal["NFC"]
    reject_empty_segments: Literal[True]
    reject_dot_segments: Literal[True]
    reject_backslashes: Literal[True]
    reject_nul: Literal[True]
    reject_casefold_collisions: Literal[True]
    reject_symlinks: Literal[True]
    reject_nested_package_roots: Literal[True]
    reject_repository_metadata: Literal[True]


class ResourceRules(StrictMarketplaceModel):
    max_files: int = Field(ge=1)
    max_traversal_entries: int = Field(ge=1)
    max_file_bytes: int = Field(ge=1)
    max_total_bytes: int = Field(ge=1)
    max_index_bytes: int = Field(ge=1)
    max_catalog_entries: int = Field(ge=1)


class DigestRules(StrictMarketplaceModel):
    algorithm: Literal["sha256"]
    bytes: Literal["exact_no_normalization"]
    ordering: Literal["unicode_code_point_by_canonical_path"]
    included_paths: Literal["all_regular_files_below_package_root"]
    excluded_paths: list[str]
    domain_separator_base64: str
    path_length_encoding: Literal["unsigned_64_bit_big_endian"]
    file_size_encoding: Literal["unsigned_64_bit_big_endian"]
    file_hash_encoding: Literal["raw_32_byte_sha256"]


class CompatibilityRules(StrictMarketplaceModel):
    supported_contract_versions: list[int]
    supported_manifest_schema_versions: list[int]
    supported_index_schema_versions: list[int]
    semantic_version_standard: Literal["SemVer 2.0.0"]
    version_comparison: Literal["SemVer_2.0.0_precedence_build_metadata_ignored"]
    unsupported_versions: Literal["fail_closed"]
    missing_external_requirements: Literal["advisory"]


class WorkflowPackageContract(StrictMarketplaceModel):
    """Loaded versioned contract envelope distributed to all consumers."""

    contract_version: Literal[1]
    package_manifest_schema: dict[str, Any]
    marketplace_index_schema: dict[str, Any]
    digests_schema: dict[str, Any]
    path_rules: PathRules
    resource_rules: ResourceRules
    digest_rules: DigestRules
    compatibility_rules: CompatibilityRules
    diagnostic_codes: dict[str, str]


class WorkflowMarketplaceSource(StrictMarketplaceModel):
    """Credential-free source configuration persisted by the marketplace."""

    name: str = Field(min_length=1, max_length=64, pattern=SOURCE_NAME_PATTERN)
    repository_url: str = Field(alias="repositoryUrl", min_length=1, max_length=4096)
    ref: str | None = Field(default=None, min_length=1, max_length=1024)
    enabled: bool = True

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: str) -> str:
        return _require_credential_free_repository_identity(value)

    @field_validator("ref")
    @classmethod
    def validate_source_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_clean_text(value, label="source value")


class InstallRequest(StrictMarketplaceModel):
    """Credential-free request for one source package or direct Git package."""

    identifier: str = Field(min_length=1, max_length=4096)
    ref: str | None = Field(default=None, min_length=1, max_length=1024)
    package_path: str | None = Field(
        default=None,
        alias="packagePath",
        min_length=1,
        max_length=1024,
        json_schema_extra={"pattern": CANONICAL_RELATIVE_PATH_PATTERN},
    )

    @field_validator("identifier")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return _require_credential_free_repository_identity(value)

    @field_validator("ref")
    @classmethod
    def validate_request_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_clean_text(value, label="request value")

    @field_validator("package_path")
    @classmethod
    def validate_package_path(cls, value: str | None) -> str | None:
        return None if value is None else _require_canonical_relative_path(value)


class PackageDiagnostic(StrictMarketplaceModel):
    code: str = Field(min_length=1, max_length=128)
    message: BoundedText
    severity: Literal["blocker", "advisory"]


class PackageReviewAssessment(StrictMarketplaceModel):
    """Bounded review projection shared by install, update, and trust flows."""

    package_digest: str = Field(alias="packageDigest", pattern=SHA256_PATTERN)
    review_digest: str = Field(alias="reviewDigest", pattern=SHA256_PATTERN)
    workflow_names: list[ShortText] = Field(
        alias="workflowNames", min_length=1, max_length=512
    )
    blockers: list[PackageDiagnostic] = Field(max_length=512)
    advisories: list[PackageDiagnostic] = Field(max_length=512)
    external_requirements: ExternalRequirements = Field(alias="externalRequirements")
    package_resources: list[str] = Field(alias="packageResources", max_length=512)

    @field_validator("workflow_names")
    @classmethod
    def validate_workflow_names(cls, value: list[str]) -> list[str]:
        return _require_unique_text(value, label="workflow names")

    @field_validator("package_resources")
    @classmethod
    def validate_package_resources(cls, value: list[str]) -> list[str]:
        paths = [_require_canonical_relative_path(path) for path in value]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("package review resources must be unique and sorted")
        return paths


class InstalledPackageIdentity(StrictMarketplaceModel):
    source_key: str = Field(alias="sourceKey", min_length=1, max_length=128)
    package_id: str = Field(alias="packageId", pattern=PACKAGE_ID_PATTERN)


class InstalledPackageProvenance(StrictMarketplaceModel):
    """Credential-free exact installation authority stored outside packages."""

    schema_version: Literal[1] = Field(alias="schemaVersion")
    identity: InstalledPackageIdentity
    source_name: str = Field(alias="sourceName", min_length=1, max_length=64)
    repository_url: str = Field(alias="repositoryUrl", min_length=1, max_length=4096)
    configured_ref: str | None = Field(
        default=None, alias="configuredRef", max_length=1024
    )
    resolved_commit: str = Field(alias="resolvedCommit", pattern=r"^[0-9a-f]{40}$")
    package_path: str = Field(alias="packagePath", min_length=1, max_length=1024)
    package_version: str = Field(alias="packageVersion", min_length=1, max_length=128)
    contract_version: Literal[1] = Field(alias="contractVersion")
    distribution_digest: str = Field(alias="distributionDigest", pattern=SHA256_PATTERN)
    installed_at: str = Field(alias="installedAt", min_length=20, max_length=64)
    actor: str = Field(min_length=1, max_length=256)
    workflow_paths: list[str] = Field(
        alias="workflowPaths", min_length=1, max_length=512
    )

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: str) -> str:
        return _require_credential_free_repository_identity(value)

    @field_validator("package_path")
    @classmethod
    def validate_package_path(cls, value: str) -> str:
        return _require_canonical_relative_path(value)

    @field_validator("package_version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if _SEMANTIC_VERSION.fullmatch(value) is None:
            raise ValueError("package version must be a semantic version")
        return value

    @field_validator("workflow_paths")
    @classmethod
    def validate_workflow_paths(cls, value: list[str]) -> list[str]:
        paths = [_require_canonical_relative_path(path) for path in value]
        if _paths_have_canonical_collision(paths):
            raise ValueError("workflow paths must be unique")
        return paths


class StringSetChange(StrictMarketplaceModel):
    """One bounded sorted set difference used by lifecycle reviews."""

    added: list[ShortText] = Field(max_length=512)
    removed: list[ShortText] = Field(max_length=512)

    @field_validator("added", "removed")
    @classmethod
    def validate_values(cls, value: list[str]) -> list[str]:
        if value != sorted(value) or len(value) != len(set(value)):
            raise ValueError("review set changes must be unique and sorted")
        return value


class WorkflowRiskIdentity(StrictMarketplaceModel):
    """One structured workflow risk identity used in update differences."""

    workflow_name: ShortText = Field(alias="workflowName")
    package_digest: str = Field(alias="packageDigest", pattern=SHA256_PATTERN)
    risk_digest: str = Field(alias="riskDigest", pattern=SHA256_PATTERN)


class WorkflowRiskChanges(StrictMarketplaceModel):
    added: list[WorkflowRiskIdentity] = Field(max_length=512)
    removed: list[WorkflowRiskIdentity] = Field(max_length=512)

    @field_validator("added", "removed")
    @classmethod
    def validate_values(
        cls, value: list[WorkflowRiskIdentity]
    ) -> list[WorkflowRiskIdentity]:
        keys = [
            (item.workflow_name, item.package_digest, item.risk_digest)
            for item in value
        ]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("workflow risk changes must be unique and sorted")
        return value


class WorkflowCompatibilityIdentity(StrictMarketplaceModel):
    """One structured per-workflow compatibility finding identity."""

    workflow_name: ShortText = Field(alias="workflowName")
    code: str = Field(min_length=1, max_length=128)
    severity: Literal["blocker", "advisory"]


class WorkflowCompatibilityChanges(StrictMarketplaceModel):
    added: list[WorkflowCompatibilityIdentity] = Field(max_length=512)
    removed: list[WorkflowCompatibilityIdentity] = Field(max_length=512)

    @field_validator("added", "removed")
    @classmethod
    def validate_values(
        cls, value: list[WorkflowCompatibilityIdentity]
    ) -> list[WorkflowCompatibilityIdentity]:
        keys = [(item.workflow_name, item.code, item.severity) for item in value]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("workflow compatibility changes must be unique and sorted")
        return value


class RequirementChanges(StrictMarketplaceModel):
    runtimes: StringSetChange
    tools: StringSetChange
    providers: StringSetChange
    services: StringSetChange
    secrets: StringSetChange


class FileDigestChange(StrictMarketplaceModel):
    """A bounded digest/path change; reviews never carry textual file diffs."""

    path: str = Field(min_length=1, max_length=1024)
    kind: Literal["added", "modified", "removed", "renamed"]
    old_path: str | None = Field(default=None, alias="oldPath", max_length=1024)
    old_digest: str | None = Field(
        default=None, alias="oldDigest", pattern=SHA256_PATTERN
    )
    candidate_digest: str | None = Field(
        default=None, alias="candidateDigest", pattern=SHA256_PATTERN
    )

    @field_validator("path", "old_path")
    @classmethod
    def validate_paths(cls, value: str | None) -> str | None:
        return None if value is None else _require_canonical_relative_path(value)

    @model_validator(mode="after")
    def validate_shape(self) -> "FileDigestChange":
        if self.kind == "added" and (
            self.old_path is not None
            or self.old_digest is not None
            or self.candidate_digest is None
        ):
            raise ValueError("added file review is inconsistent")
        if self.kind == "removed" and (
            self.old_path is not None
            or self.old_digest is None
            or self.candidate_digest is not None
        ):
            raise ValueError("removed file review is inconsistent")
        if self.kind == "modified" and (
            self.old_path is not None
            or self.old_digest is None
            or self.candidate_digest is None
        ):
            raise ValueError("modified file review is inconsistent")
        if self.kind == "renamed" and (
            self.old_path is None
            or self.old_digest is None
            or self.candidate_digest is None
            or self.old_digest != self.candidate_digest
        ):
            raise ValueError("renamed file review is inconsistent")
        return self


class WorkflowTrustReviewItem(StrictMarketplaceModel):
    """Exact per-workflow risk and trust identity rendered before a grant."""

    workflow_name: ShortText = Field(alias="workflowName")
    definition_path: str = Field(alias="definitionPath", max_length=1024)
    companion_path: str | None = Field(
        default=None, alias="companionPath", max_length=1024
    )
    package_digest: str = Field(alias="packageDigest", pattern=SHA256_PATTERN)
    risk_digest: str = Field(alias="riskDigest", pattern=SHA256_PATTERN)
    trust_state: Literal["trusted", "untrusted"] = Field(alias="trustState")
    shell_or_script_nodes: list[ShortText] = Field(
        alias="shellOrScriptNodes", max_length=512
    )
    command_nodes: list[ShortText] = Field(alias="commandNodes", max_length=512)
    approval_nodes: list[ShortText] = Field(alias="approvalNodes", max_length=512)
    command_resources: list[str] = Field(alias="commandResources", max_length=512)
    script_resources: list[str] = Field(alias="scriptResources", max_length=512)
    mcp_resources: list[str] = Field(alias="mcpResources", max_length=512)
    requested_tools: list[ShortText] = Field(alias="requestedTools", max_length=512)
    requested_skills: list[ShortText] = Field(alias="requestedSkills", max_length=512)
    local_mcp_servers: list[ShortText] = Field(alias="localMcpServers", max_length=512)
    remote_mcp_servers: list[ShortText] = Field(
        alias="remoteMcpServers", max_length=512
    )
    providers: list[ShortText] = Field(max_length=512)
    outward_action_nodes: list[ShortText] = Field(
        alias="outwardActionNodes", max_length=512
    )
    required_secrets: list[ShortText] = Field(alias="requiredSecrets", max_length=512)
    external_requirements: ExternalRequirements = Field(alias="externalRequirements")
    package_resource_set: Literal["package"] = Field(alias="packageResourceSet")
    compatibility: list[PackageDiagnostic] = Field(max_length=512)

    @field_validator(
        "definition_path",
        "companion_path",
        "command_resources",
        "script_resources",
        "mcp_resources",
    )
    @classmethod
    def validate_resource_paths(cls, value):
        if value is None:
            return None
        if isinstance(value, list):
            paths = [_require_canonical_relative_path(path) for path in value]
            if paths != sorted(paths) or len(paths) != len(set(paths)):
                raise ValueError("review resource paths must be unique and sorted")
            return paths
        return _require_canonical_relative_path(value)


class InstalledPackage(StrictMarketplaceModel):
    identity: InstalledPackageIdentity
    source_name: str = Field(alias="sourceName", min_length=1, max_length=64)
    repository_url: str = Field(alias="repositoryUrl", min_length=1, max_length=4096)
    configured_ref: str | None = Field(default=None, alias="configuredRef")
    resolved_commit: str = Field(alias="resolvedCommit", pattern=r"^[0-9a-f]{40}$")
    package_path: str = Field(alias="packagePath", min_length=1, max_length=1024)
    version: str = Field(pattern=SEMANTIC_VERSION_PATTERN)
    contract_version: Literal[1] = Field(alias="contractVersion")
    distribution_digest: str = Field(alias="distributionDigest", pattern=SHA256_PATTERN)
    installed_at: str = Field(alias="installedAt", min_length=20, max_length=64)
    actor: str = Field(min_length=1, max_length=256)
    workflow_paths: list[str] = Field(alias="workflowPaths", max_length=512)
    orphaned_source: bool = Field(alias="orphanedSource")

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: str) -> str:
        return _require_credential_free_repository_identity(value)


class InstallReview(StrictMarketplaceModel):
    operation: Literal["install"]
    result: Literal["review_required"] = "review_required"
    confirmation_token: str = Field(
        alias="confirmationToken", min_length=1, max_length=4096
    )
    review_digest: str = Field(alias="reviewDigest", pattern=SHA256_PATTERN)
    identity: InstalledPackageIdentity
    source_name: str = Field(alias="sourceName", min_length=1, max_length=64)
    repository_url: str = Field(alias="repositoryUrl", min_length=1, max_length=4096)
    configured_ref: str | None = Field(default=None, alias="configuredRef")
    resolved_commit: str = Field(alias="resolvedCommit", pattern=r"^[0-9a-f]{40}$")
    package_path: str = Field(alias="packagePath", min_length=1, max_length=1024)
    candidate_version: str = Field(
        alias="candidateVersion", pattern=SEMANTIC_VERSION_PATTERN
    )
    candidate_digest: str = Field(alias="candidateDigest", pattern=SHA256_PATTERN)
    assessment: PackageReviewAssessment
    file_changes: list[FileDigestChange] = Field(alias="fileChanges", max_length=1024)
    workflow_reviews: list[WorkflowTrustReviewItem] = Field(
        alias="workflowReviews", max_length=512
    )


class UpdateReview(StrictMarketplaceModel):
    operation: Literal["update"]
    result: Literal["review_required", "update_available", "unchanged"]
    confirmation_token: str | None = Field(
        default=None, alias="confirmationToken", max_length=4096
    )
    review_digest: str = Field(alias="reviewDigest", pattern=SHA256_PATTERN)
    identity: InstalledPackageIdentity
    source_name: str = Field(alias="sourceName", min_length=1, max_length=64)
    repository_url: str = Field(alias="repositoryUrl", min_length=1, max_length=4096)
    configured_ref: str | None = Field(default=None, alias="configuredRef")
    old_version: str = Field(alias="oldVersion", pattern=SEMANTIC_VERSION_PATTERN)
    candidate_version: str = Field(
        alias="candidateVersion", pattern=SEMANTIC_VERSION_PATTERN
    )
    old_commit: str = Field(alias="oldCommit", pattern=r"^[0-9a-f]{40}$")
    candidate_commit: str = Field(alias="candidateCommit", pattern=r"^[0-9a-f]{40}$")
    old_digest: str = Field(alias="oldDigest", pattern=SHA256_PATTERN)
    candidate_digest: str = Field(alias="candidateDigest", pattern=SHA256_PATTERN)
    file_changes: list[FileDigestChange] = Field(alias="fileChanges", max_length=1024)
    workflow_changes: StringSetChange = Field(alias="workflowChanges")
    requirement_changes: RequirementChanges = Field(alias="requirementChanges")
    risk_changes: WorkflowRiskChanges = Field(alias="riskChanges")
    compatibility_changes: WorkflowCompatibilityChanges = Field(
        alias="compatibilityChanges"
    )
    assessment: PackageReviewAssessment
    workflow_reviews: list[WorkflowTrustReviewItem] = Field(
        alias="workflowReviews", max_length=512
    )


class RemoveReview(StrictMarketplaceModel):
    operation: Literal["remove"]
    result: Literal["review_required"] = "review_required"
    confirmation_token: str = Field(
        alias="confirmationToken", min_length=1, max_length=4096
    )
    review_digest: str = Field(alias="reviewDigest", pattern=SHA256_PATTERN)
    identity: InstalledPackageIdentity
    current_version: str = Field(
        alias="currentVersion", pattern=SEMANTIC_VERSION_PATTERN
    )
    current_commit: str = Field(alias="currentCommit", pattern=r"^[0-9a-f]{40}$")
    distribution_digest: str = Field(alias="distributionDigest", pattern=SHA256_PATTERN)
    workflow_names: list[ShortText] = Field(alias="workflowNames", max_length=512)


class TrustReview(StrictMarketplaceModel):
    confirmation_token: str = Field(
        alias="confirmationToken", min_length=1, max_length=4096
    )
    review_digest: str = Field(alias="reviewDigest", pattern=SHA256_PATTERN)
    identity: InstalledPackageIdentity
    source_name: str = Field(alias="sourceName", min_length=1, max_length=64)
    version: str = Field(pattern=SEMANTIC_VERSION_PATTERN)
    resolved_commit: str = Field(alias="resolvedCommit", pattern=r"^[0-9a-f]{40}$")
    distribution_digest: str = Field(alias="distributionDigest", pattern=SHA256_PATTERN)
    package_resources: list[str] = Field(alias="packageResources", max_length=512)
    workflows: list[WorkflowTrustReviewItem] = Field(min_length=1, max_length=512)

    @field_validator("package_resources")
    @classmethod
    def validate_package_resources(cls, value: list[str]) -> list[str]:
        paths = [_require_canonical_relative_path(path) for path in value]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("trust review resources must be unique and sorted")
        return paths


class UpdateCheck(StrictMarketplaceModel):
    identity: InstalledPackageIdentity
    status: Literal["current", "update_available", "orphaned", "error"]
    installed_version: str = Field(
        alias="installedVersion", pattern=SEMANTIC_VERSION_PATTERN
    )
    candidate_version: str | None = Field(
        default=None, alias="candidateVersion", pattern=SEMANTIC_VERSION_PATTERN
    )
    diagnostic_code: str | None = Field(
        default=None, alias="diagnosticCode", min_length=1, max_length=128
    )
    message: BoundedText | None = None

    @model_validator(mode="after")
    def validate_diagnostic(self) -> "UpdateCheck":
        if self.status == "error":
            if self.diagnostic_code is None or self.message is None:
                raise ValueError("failed update check requires a diagnostic")
        elif self.diagnostic_code is not None or self.message is not None:
            raise ValueError("successful update check cannot contain a diagnostic")
        return self


__all__ = [
    "CompatibilityRules",
    "DigestRules",
    "ExternalRequirements",
    "InstallRequest",
    "InstallReview",
    "InstalledPackage",
    "InstalledPackageIdentity",
    "InstalledPackageProvenance",
    "PackageDiagnostic",
    "PackageReviewAssessment",
    "FileDigestChange",
    "RemoveReview",
    "RequirementChanges",
    "StringSetChange",
    "TrustReview",
    "UpdateCheck",
    "UpdateReview",
    "WorkflowTrustReviewItem",
    "WorkflowCompatibilityChanges",
    "WorkflowCompatibilityIdentity",
    "WorkflowRiskChanges",
    "WorkflowRiskIdentity",
    "PathRules",
    "ResourceRules",
    "WorkflowMarketplaceSource",
    "WorkflowMember",
    "WorkflowPackageContract",
    "WorkflowPackageDigestRecord",
    "WorkflowPackageDigests",
    "WorkflowPackageIndex",
    "WorkflowPackageIndexEntry",
    "WorkflowPackageManifest",
]
