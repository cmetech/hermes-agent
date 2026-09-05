"""Strict version-two lifecycle projections and publication correlations."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import unicodedata
from types import MappingProxyType, UnionType
from typing import Annotated, Literal, Self, TypeAlias, Union, get_args, get_origin

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from .models import (
    ExternalRequirements,
    FileDigestChange,
    InstalledPackage,
    InstalledPackageIdentity,
    PackageDiagnostic,
    PackageInspection,
    PackageInspectionResource,
    PackageReviewAssessment,
    RequirementChanges,
    SHA256_PATTERN,
    SOURCE_NAME_PATTERN,
    SEMANTIC_VERSION_PATTERN,
    ShortText,
    StringSetChange,
    UpdateCheck,
    WorkflowCompatibilityChanges,
    WorkflowRiskChanges,
    WorkflowTrustReviewItem,
    _require_clean_text,
    _require_credential_free_repository_identity,
)


LifecycleKind = Literal[
    "refresh",
    "inspect",
    "update_check",
    "install_prepare",
    "update_prepare",
    "remove_prepare",
    "trust_prepare",
    "install_confirm",
    "update_confirm",
    "remove_confirm",
    "trust_confirm",
    "trust_revoke",
]
LifecycleState = Literal["pending", "running", "succeeded", "failed", "cancelled"]
LifecyclePhase = Literal[
    "queued",
    "running",
    "fetching",
    "reviewing",
    "validating",
    "committing",
    "recovering",
    "completed",
    "failed",
    "cancelled",
]

_OPERATION_ID_PATTERN = r"^wmop_[0-9a-f]{12}_[0-9a-f]{32}$"
_EPOCH_PATTERN = r"^[0-9a-f]{32}$"
_REQUEST_ID = re.compile(r"^wmreq_([0-9a-f]{32})_[0-9]{13}_[0-9a-f]{32}$", re.ASCII)
_SOURCE_KEY_PATTERN = r"^[a-z0-9](?:[a-z0-9_-]{0,126}[a-z0-9])?$"
_PACKAGE_ID_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
_IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_]{0,127}$"
_HEX64_PATTERN = r"^[0-9a-f]{64}$"
_RESULT_BYTES_MAX = 2 * 1024 * 1024


def lifecycle_relative_path(value: str) -> str:
    """Validate the original V2 path without changing V1 package semantics."""
    if (
        not 1 <= len(value) <= 1024
        or unicodedata.normalize("NFC", value) != value
        or any(
            unicodedata.category(char) == "Cc" or char in "\u2028\u2029"
            for char in value
        )
        or "\\" in value
    ):
        raise ValueError("path must be a canonical lifecycle-relative path")
    if any(
        part in {"", ".", ".."}
        or re.match(r"[A-Za-z]:", part)
        or part.casefold() == ".git"
        for part in value.split("/")
    ):
        raise ValueError("path must be a canonical lifecycle-relative path")
    return value


LifecycleRelativePath = Annotated[
    str,
    Field(
        min_length=1,
        max_length=1024,
        json_schema_extra={"x-hermes-domain": "lifecycle_relative_path"},
    ),
    AfterValidator(lifecycle_relative_path),
]

# These reused V1 value fields acquire the V2 domain only at the projection
# boundary. The same inventory marks their schema for downstream generation.
_REUSED_PATH_FIELDS = {
    InstalledPackage: {"package_path", "workflow_paths"},
    PackageInspection: {"package_path"},
    PackageInspectionResource: {"path"},
    PackageReviewAssessment: {"package_resources"},
    FileDigestChange: {"path", "old_path"},
    WorkflowTrustReviewItem: {
        "definition_path",
        "companion_path",
        "command_resources",
        "script_resources",
        "mcp_resources",
        "mcp_resource_files",
    },
}


def _mark_path_schema(schema: dict) -> None:
    if schema.get("type") == "array":
        _mark_path_schema(schema["items"])
    elif "anyOf" in schema:
        for variant in schema["anyOf"]:
            if variant.get("type") != "null":
                _mark_path_schema(variant)
    else:
        schema.pop("pattern", None)
        schema.update(minLength=1, maxLength=1024)
        schema["x-hermes-domain"] = "lifecycle_relative_path"


class StrictLifecycleModel(BaseModel):
    """Base for strict immutable snake-case lifecycle wire objects."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        schema = super().model_json_schema(*args, **kwargs)
        definitions = schema.get("$defs", {})
        for model, fields in _REUSED_PATH_FIELDS.items():
            definition = definitions.get(model.__name__, {})
            properties = definition.get("properties", {})
            for name in fields:
                field = model.model_fields[name]
                key = name if name in properties else field.alias
                if key in properties:
                    _mark_path_schema(properties[key])
        return schema

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: Literal["allow", "ignore", "forbid"] | None = None,
        context: object | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        """Reject duplicate object keys before Pydantic decodes the document."""

        validation_message = None
        try:
            json.loads(json_data, object_pairs_hook=_unique_json_object)
        except _DuplicateJsonKeyError:
            validation_message = "duplicate JSON object key"
        except (json.JSONDecodeError, UnicodeDecodeError):
            validation_message = "invalid JSON document"
        if validation_message is not None:
            raise _json_validation_error(cls.__name__, validation_message)
        return super().model_validate_json(
            json_data,
            strict=strict,
            extra=extra,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )


class _DuplicateJsonKeyError(ValueError):
    """Private sentinel used to preserve a stable public validation error."""


def _json_validation_error(model_name: str, message: str) -> ValidationError:
    return ValidationError.from_exception_data(
        model_name,
        [
            {
                "type": "value_error",
                "loc": (),
                "input": "JSON document",
                "ctx": {"error": ValueError(message)},
            }
        ],
        input_type="json",
        hide_input=True,
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKeyError
        value[key] = item
    return value


def _canonical_utc(value: str | None, *, label: str) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be canonical UTC") from error
    canonical = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if not value.endswith("Z") or value != canonical:
        raise ValueError(f"{label} must be canonical UTC")
    return value


LifecycleCapability = Literal[
    "operations",
    "admission_replay",
    "package_state",
    "transactions",
    "updates",
    "trust",
    "sources",
    "inspect",
]
LIFECYCLE_CAPABILITIES = get_args(LifecycleCapability)

LifecycleHttpErrorCode = Literal[
    "marketplace_admission_capacity",
    "marketplace_admission_not_found",
    "marketplace_epoch_changed",
    "marketplace_internal_error",
    "marketplace_list_capacity",
    "marketplace_list_expired",
    "marketplace_operation_capacity",
    "marketplace_operation_conflict",
    "marketplace_operation_not_found",
    "marketplace_operation_unavailable",
    "marketplace_request_conflict",
    "marketplace_request_expired",
    "marketplace_request_invalid",
    "marketplace_review_unavailable",
]
LIFECYCLE_HTTP_ERROR_CODES = frozenset(get_args(LifecycleHttpErrorCode))


class LifecycleCapabilities(StrictLifecycleModel):
    schema_version: Literal[2]
    profile: str = Field(min_length=1, max_length=256)
    registry_epoch: str = Field(pattern=_EPOCH_PATTERN)
    server_time: str = Field(min_length=20, max_length=64)
    capabilities: list[LifecycleCapability] = Field(max_length=8)

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_version(cls, value):
        if type(value) is not int or value != 2:
            raise ValueError("schema_version must be the integer 2")
        return value

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        return _require_clean_text(value, label="profile")

    @field_validator("server_time")
    @classmethod
    def validate_server_time(cls, value: str) -> str:
        return _canonical_utc(value, label="server time")

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, value):
        if value != [item for item in LIFECYCLE_CAPABILITIES if item in value]:
            raise ValueError("capabilities must be a unique ordered subsequence")
        return value


def _convert_snake_annotation(annotation: object, value: object) -> object:
    origin = get_origin(annotation)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _snake_payload_for(annotation, value)
    if origin is list and isinstance(value, list):
        item_type = get_args(annotation)[0]
        return [_convert_snake_annotation(item_type, item) for item in value]
    if origin in (Union, UnionType):
        for candidate in get_args(annotation):
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                if isinstance(value, dict):
                    return _snake_payload_for(candidate, value)
        return value
    return value


def _require_v2_contract_version(value: object) -> None:
    if type(value) is not int or value != 1:
        raise ValueError("contract_version must be the integer 1")


def _validate_v2_installed_package(value: dict[str, object]) -> None:
    if "contract_version" in value:
        _require_v2_contract_version(value["contract_version"])
    installed_at = value.get("installed_at")
    if isinstance(installed_at, str):
        _canonical_utc(installed_at, label="installed_at")
    configured_ref = value.get("configured_ref")
    if isinstance(configured_ref, str):
        _require_clean_text(configured_ref, label="configured ref")
    actor = value.get("actor")
    if isinstance(actor, str):
        _require_clean_text(actor, label="actor")
    workflow_paths = value.get("workflow_paths")
    if isinstance(workflow_paths, list) and all(
        isinstance(path, str) for path in workflow_paths
    ):
        if len(workflow_paths) != len({path.casefold() for path in workflow_paths}):
            raise ValueError("workflow_paths must be unique canonical paths")
    identity = value.get("identity")
    source_name = value.get("source_name")
    if (
        isinstance(identity, dict)
        and isinstance(source_name, str)
        and identity.get("source_key") != source_name
    ):
        raise ValueError("installed package identity is inconsistent")


def _validate_v2_package_inspection(value: dict[str, object]) -> None:
    if "contract_version" in value:
        _require_v2_contract_version(value["contract_version"])
    verified = value.get("verified")
    if verified is not None and (type(verified) is not bool or not verified):
        raise ValueError("verified must be true")
    verified_at = value.get("verified_at")
    if isinstance(verified_at, str):
        _canonical_utc(verified_at, label="verified_at")


def _validate_v2_reused_payload(
    model: type[BaseModel], value: dict[str, object]
) -> None:
    for name in _REUSED_PATH_FIELDS.get(model, ()):
        paths = value.get(name)
        for path in paths if isinstance(paths, list) else [paths]:
            if isinstance(path, str):
                lifecycle_relative_path(path)
    if model is InstalledPackageIdentity:
        PackageIdentity.model_validate(value)
    elif model is InstalledPackage:
        _validate_v2_installed_package(value)
    elif model is PackageInspection:
        _validate_v2_package_inspection(value)


def _snake_payload_for(model: type[BaseModel], value: object) -> object:
    """Translate an exact snake-case wire object for a reused V1 value model."""

    if isinstance(value, model):
        value = value.model_dump(mode="json", by_alias=False)
    if not isinstance(value, dict):
        return value
    _validate_v2_reused_payload(model, value)
    translated: dict[str, object] = {}
    for key, item in value.items():
        field = model.model_fields.get(key)
        if field is None:
            raise ValueError("version-two projection keys must use snake_case")
        alias = field.alias if isinstance(field.alias, str) else key
        translated[alias] = _convert_snake_annotation(field.annotation, item)
    return translated


def _snake_projection(model: type[BaseModel]):
    return Annotated[
        model,
        BeforeValidator(lambda value: _snake_payload_for(model, value)),
    ]


SnakeExternalRequirements = _snake_projection(ExternalRequirements)
SnakeFileDigestChange = _snake_projection(FileDigestChange)
SnakeInstalledPackage = _snake_projection(InstalledPackage)
SnakePackageDiagnostic = _snake_projection(PackageDiagnostic)
SnakePackageInspection = _snake_projection(PackageInspection)
SnakePackageReviewAssessment = _snake_projection(PackageReviewAssessment)
SnakeRequirementChanges = _snake_projection(RequirementChanges)
SnakeStringSetChange = _snake_projection(StringSetChange)
SnakeUpdateCheck = _snake_projection(UpdateCheck)
SnakeWorkflowCompatibilityChanges = _snake_projection(WorkflowCompatibilityChanges)
SnakeWorkflowRiskChanges = _snake_projection(WorkflowRiskChanges)
SnakeWorkflowTrustReviewItem = _snake_projection(WorkflowTrustReviewItem)


class PackageIdentity(StrictLifecycleModel):
    source_key: str = Field(min_length=1, max_length=128, pattern=_SOURCE_KEY_PATTERN)
    package_id: str = Field(min_length=1, max_length=64, pattern=_PACKAGE_ID_PATTERN)


class SourceSubject(StrictLifecycleModel):
    type: Literal["source"]
    source_name: str = Field(min_length=1, max_length=64, pattern=SOURCE_NAME_PATTERN)


class PackageSubject(StrictLifecycleModel):
    type: Literal["package"]
    identity: PackageIdentity


class AllPackagesSubject(StrictLifecycleModel):
    type: Literal["all_packages"]


class DirectInstallSubject(StrictLifecycleModel):
    type: Literal["direct_install"]
    source_key: str = Field(min_length=1, max_length=128, pattern=_SOURCE_KEY_PATTERN)
    selector_id: str = Field(pattern=_HEX64_PATTERN)


LifecycleSubject: TypeAlias = Annotated[
    SourceSubject | PackageSubject | AllPackagesSubject | DirectInstallSubject,
    Field(discriminator="type"),
]


class AllTrustSelection(StrictLifecycleModel):
    type: Literal["all"]


class OneTrustSelection(StrictLifecycleModel):
    type: Literal["one"]
    workflow_name: ShortText

    @field_validator("workflow_name")
    @classmethod
    def validate_workflow_name(cls, value: str) -> str:
        return _require_clean_text(value, label="workflow name")


TrustSelection: TypeAlias = Annotated[
    AllTrustSelection | OneTrustSelection,
    Field(discriminator="type"),
]


class WorkflowInventoryItem(StrictLifecycleModel):
    workflow_name: ShortText
    definition_path: LifecycleRelativePath

    @field_validator("workflow_name")
    @classmethod
    def validate_workflow_name(cls, value: str) -> str:
        return _require_clean_text(value, label="workflow name")


class TrustWorkflowState(WorkflowInventoryItem):
    state: Literal["trusted", "untrusted"]


def _require_unique_trust_workflows(
    value: list[TrustWorkflowState], *, label: str
) -> list[TrustWorkflowState]:
    names = [item.workflow_name for item in value]
    paths = [item.definition_path for item in value]
    if len(names) != len(set(names)) or len(paths) != len(set(paths)):
        raise ValueError(f"{label} workflows must be unique")
    return value


class TrustSnapshot(StrictLifecycleModel):
    identity: PackageIdentity
    distribution_digest: str = Field(pattern=SHA256_PATTERN)
    workflows: list[TrustWorkflowState] = Field(max_length=512)

    @field_validator("workflows")
    @classmethod
    def require_unique_workflows(
        cls, value: list[TrustWorkflowState]
    ) -> list[TrustWorkflowState]:
        return _require_unique_trust_workflows(value, label="trust snapshot")


class PackageState(StrictLifecycleModel):
    profile: str = Field(min_length=1, max_length=256)
    identity: PackageIdentity
    observed_at: str = Field(min_length=20, max_length=64)
    state: Literal["installed", "absent", "unconfirmed"]
    installed: SnakeInstalledPackage | None
    trust: TrustSnapshot | None
    recovery: Literal["clear", "required", "unconfirmed"]
    busy: StrictBool

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        return _require_clean_text(value, label="profile")

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: str) -> str:
        checked = _canonical_utc(value, label="observed_at")
        assert checked is not None
        return checked

    @model_validator(mode="after")
    def validate_state_shape(self) -> "PackageState":
        if self.state == "installed":
            if self.installed is None or self.trust is None or self.recovery != "clear":
                raise ValueError("installed package state is not verified")
            installed_identity = PackageIdentity.model_validate(
                self.installed.identity.model_dump(mode="json", by_alias=False)
            )
            if (
                installed_identity != self.identity
                or self.trust.identity != self.identity
                or self.trust.distribution_digest != self.installed.distribution_digest
            ):
                raise ValueError("installed package state identity is inconsistent")
            paths = [item.definition_path for item in self.trust.workflows]
            if len(self.installed.workflow_paths) != len(
                set(self.installed.workflow_paths)
            ) or set(paths) != set(self.installed.workflow_paths):
                raise ValueError("installed package trust membership is incomplete")
        elif self.state == "absent":
            if (
                self.installed is not None
                or self.trust is not None
                or self.recovery != "clear"
            ):
                raise ValueError("absent package state is inconsistent")
        elif self.installed is not None or self.trust is not None:
            raise ValueError("unconfirmed package state cannot claim installed data")
        if self.recovery != "clear" and self.state != "unconfirmed":
            raise ValueError("unclear recovery requires unconfirmed package state")
        return self


class CommittedOutcome(StrictLifecycleModel):
    type: Literal["committed"]
    package_state: PackageState | None


class KnownUnchangedOutcome(StrictLifecycleModel):
    type: Literal["known_unchanged"]
    evidence: Literal["read_only", "before_mutation", "rollback_verified"]
    package_state: PackageState | None


class CancelledBeforeCommitOutcome(StrictLifecycleModel):
    type: Literal["cancelled_before_commit"]
    package_state: PackageState | None


class RecoveryRequiredOutcome(StrictLifecycleModel):
    type: Literal["recovery_required"]
    reason: Literal["rollback_failed", "recovery_ambiguous", "state_unverified"]


class OutcomeUnknown(StrictLifecycleModel):
    type: Literal["outcome_unknown"]
    reason: Literal[
        "terminal_invalid",
        "status_unavailable",
        "response_lost",
        "evicted",
        "backend_restarted",
    ]


LifecycleOutcome: TypeAlias = Annotated[
    CommittedOutcome
    | KnownUnchangedOutcome
    | CancelledBeforeCommitOutcome
    | RecoveryRequiredOutcome
    | OutcomeUnknown,
    Field(discriminator="type"),
]


class ReviewAvailability(StrictLifecycleModel):
    confirmation_available: StrictBool
    expires_at: str | None = Field(default=None, min_length=20, max_length=64)

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at(cls, value: str | None) -> str | None:
        return _canonical_utc(value, label="expires_at")

    @model_validator(mode="after")
    def require_availability_expiry(self) -> "ReviewAvailability":
        if self.confirmation_available != (self.expires_at is not None):
            raise ValueError("review availability and expiry are inconsistent")
        return self


class InstallReviewProjection(ReviewAvailability):
    operation: Literal["install"]
    result: Literal["review_required"] = "review_required"
    review_digest: str = Field(pattern=SHA256_PATTERN)
    identity: PackageIdentity
    source_name: str = Field(min_length=1, max_length=64, pattern=SOURCE_NAME_PATTERN)
    repository_url: str = Field(min_length=1, max_length=4096)
    configured_ref: str | None = Field(default=None, max_length=1024)
    resolved_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    package_path: LifecycleRelativePath
    candidate_version: str = Field(pattern=SEMANTIC_VERSION_PATTERN)
    candidate_digest: str = Field(pattern=SHA256_PATTERN)
    assessment: SnakePackageReviewAssessment
    file_changes: list[SnakeFileDigestChange] = Field(max_length=1024)
    workflow_reviews: list[SnakeWorkflowTrustReviewItem] = Field(max_length=512)

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: str) -> str:
        return _require_credential_free_repository_identity(value)

    @field_validator("configured_ref")
    @classmethod
    def validate_configured_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_clean_text(value, label="configured ref")

    @model_validator(mode="after")
    def validate_identity(self) -> "InstallReviewProjection":
        if self.source_name != self.identity.source_key:
            raise ValueError("install review identity is inconsistent")
        return self


class UpdateReviewProjection(ReviewAvailability):
    operation: Literal["update"]
    result: Literal["review_required", "update_available", "unchanged"]
    review_digest: str = Field(pattern=SHA256_PATTERN)
    identity: PackageIdentity
    source_name: str = Field(min_length=1, max_length=64, pattern=SOURCE_NAME_PATTERN)
    repository_url: str = Field(min_length=1, max_length=4096)
    configured_ref: str | None = Field(default=None, max_length=1024)
    old_version: str = Field(pattern=SEMANTIC_VERSION_PATTERN)
    candidate_version: str = Field(pattern=SEMANTIC_VERSION_PATTERN)
    old_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    candidate_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    old_digest: str = Field(pattern=SHA256_PATTERN)
    candidate_digest: str = Field(pattern=SHA256_PATTERN)
    file_changes: list[SnakeFileDigestChange] = Field(max_length=1024)
    workflow_changes: SnakeStringSetChange
    requirement_changes: SnakeRequirementChanges
    risk_changes: SnakeWorkflowRiskChanges
    compatibility_changes: SnakeWorkflowCompatibilityChanges
    assessment: SnakePackageReviewAssessment
    workflow_reviews: list[SnakeWorkflowTrustReviewItem] = Field(max_length=512)

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: str) -> str:
        return _require_credential_free_repository_identity(value)

    @field_validator("configured_ref")
    @classmethod
    def validate_configured_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_clean_text(value, label="configured ref")

    @model_validator(mode="after")
    def validate_review_shape(self) -> "UpdateReviewProjection":
        if self.source_name != self.identity.source_key:
            raise ValueError("update review identity is inconsistent")
        if self.result == "unchanged" and (
            self.confirmation_available or self.expires_at is not None
        ):
            raise ValueError("unchanged update cannot be confirmed")
        return self


class RemoveReviewProjection(ReviewAvailability):
    operation: Literal["remove"]
    result: Literal["review_required"] = "review_required"
    review_digest: str = Field(pattern=SHA256_PATTERN)
    identity: PackageIdentity
    current_version: str = Field(pattern=SEMANTIC_VERSION_PATTERN)
    current_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    distribution_digest: str = Field(pattern=SHA256_PATTERN)
    workflow_names: list[ShortText] = Field(max_length=512)


class TrustReviewProjection(ReviewAvailability):
    review_digest: str = Field(pattern=SHA256_PATTERN)
    identity: PackageIdentity
    source_name: str = Field(min_length=1, max_length=64, pattern=SOURCE_NAME_PATTERN)
    version: str = Field(pattern=SEMANTIC_VERSION_PATTERN)
    resolved_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    distribution_digest: str = Field(pattern=SHA256_PATTERN)
    package_resources: list[LifecycleRelativePath] = Field(max_length=512)
    package_workflows: list[WorkflowInventoryItem] = Field(min_length=1, max_length=512)
    workflows: list[SnakeWorkflowTrustReviewItem] = Field(min_length=1, max_length=512)

    @field_validator("package_resources")
    @classmethod
    def validate_package_resources(cls, value: list[str]) -> list[str]:
        paths = value
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("trust review resources must be unique and sorted")
        return paths

    @model_validator(mode="after")
    def validate_inventory(self) -> "TrustReviewProjection":
        if self.source_name != self.identity.source_key:
            raise ValueError("trust review identity is inconsistent")
        inventory = [
            (item.workflow_name, item.definition_path)
            for item in self.package_workflows
        ]
        if len(inventory) != len(set(inventory)):
            raise ValueError("package workflow inventory must be unique")
        names = [name for name, _path in inventory]
        paths = [path for _name, path in inventory]
        if len(names) != len(set(names)) or len(paths) != len(set(paths)):
            raise ValueError("package workflow inventory must be unique")
        reviewed = [
            (item.workflow_name, item.definition_path) for item in self.workflows
        ]
        if not set(reviewed).issubset(set(inventory)) or len(reviewed) != len(
            set(reviewed)
        ):
            raise ValueError("trust review workflows are not known inventory members")
        return self


class SourceRefreshValue(StrictLifecycleModel):
    source_name: str = Field(min_length=1, max_length=64, pattern=SOURCE_NAME_PATTERN)
    repository_url: str = Field(min_length=1, max_length=4096)
    state: Literal[
        "fresh",
        "stale",
        "disabled",
        "authentication-failed",
        "malformed",
        "incompatible",
        "unavailable",
        "cancelled",
    ]
    resolved_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    verified_at: str | None = Field(default=None, min_length=20, max_length=64)
    package_count: StrictInt = Field(ge=0, le=4096)
    diagnostic_code: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN
    )
    message: str | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: str) -> str:
        return _require_credential_free_repository_identity(value)

    @field_validator("verified_at")
    @classmethod
    def validate_verified_at(cls, value: str | None) -> str | None:
        return _canonical_utc(value, label="verified_at")


class UpdateChecksValue(StrictLifecycleModel):
    checks: list[SnakeUpdateCheck] = Field(max_length=512)


class TrustGrantValue(StrictLifecycleModel):
    identity: PackageIdentity
    selection: TrustSelection
    distribution_digest: str = Field(pattern=SHA256_PATTERN)
    workflows: list[TrustWorkflowState] = Field(max_length=512)

    @field_validator("workflows")
    @classmethod
    def require_unique_workflows(
        cls, value: list[TrustWorkflowState]
    ) -> list[TrustWorkflowState]:
        return _require_unique_trust_workflows(value, label="trust grant")

    @model_validator(mode="after")
    def require_selected_trust(self) -> "TrustGrantValue":
        states = {item.workflow_name: item.state for item in self.workflows}
        if isinstance(self.selection, AllTrustSelection):
            if any(state != "trusted" for state in states.values()):
                raise ValueError("all-workflow grant must trust every workflow")
        elif states.get(self.selection.workflow_name) != "trusted":
            raise ValueError("selected workflow grant is not trusted")
        return self


class TrustRevokeValue(StrictLifecycleModel):
    identity: PackageIdentity
    selection: TrustSelection
    distribution_digest: str = Field(pattern=SHA256_PATTERN)
    workflows: list[TrustWorkflowState] = Field(max_length=512)
    revoked: StrictInt = Field(ge=0, le=512)

    @field_validator("workflows")
    @classmethod
    def require_unique_workflows(
        cls, value: list[TrustWorkflowState]
    ) -> list[TrustWorkflowState]:
        return _require_unique_trust_workflows(value, label="trust revoke")

    @model_validator(mode="after")
    def require_selected_inventory_member(self) -> "TrustRevokeValue":
        if isinstance(
            self.selection, OneTrustSelection
        ) and self.selection.workflow_name not in {
            item.workflow_name for item in self.workflows
        }:
            raise ValueError("selected workflow is not in the trust inventory")
        return self


class _LifecycleResult(StrictLifecycleModel):
    @model_validator(mode="after")
    def require_bounded_json(self) -> "_LifecycleResult":
        rendered = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(rendered) > _RESULT_BYTES_MAX:
            raise ValueError("operation result exceeds its byte limit")
        return self


class SourceRefreshResult(_LifecycleResult):
    type: Literal["source_refresh"]
    value: SourceRefreshValue


class PackageDetailResult(_LifecycleResult):
    type: Literal["package_detail"]
    value: SnakePackageInspection


class UpdateChecksResult(_LifecycleResult):
    type: Literal["update_checks"]
    value: UpdateChecksValue


class InstallReviewResult(_LifecycleResult):
    type: Literal["install_review"]
    value: InstallReviewProjection


class InstalledPackageResult(_LifecycleResult):
    type: Literal["installed_package"]
    value: SnakeInstalledPackage


class UpdateReviewResult(_LifecycleResult):
    type: Literal["update_review"]
    value: UpdateReviewProjection


class UpdatedPackageResult(_LifecycleResult):
    type: Literal["updated_package"]
    value: SnakeInstalledPackage


class RemoveReviewResult(_LifecycleResult):
    type: Literal["remove_review"]
    value: RemoveReviewProjection


class RemovedPackageResult(_LifecycleResult):
    type: Literal["removed_package"]
    value: SnakeInstalledPackage


class TrustReviewResult(_LifecycleResult):
    type: Literal["trust_review"]
    value: TrustReviewProjection


class TrustGrantResult(_LifecycleResult):
    type: Literal["trust_grant"]
    value: TrustGrantValue


class TrustRevokeResult(_LifecycleResult):
    type: Literal["trust_revoke"]
    value: TrustRevokeValue


LifecycleResult: TypeAlias = Annotated[
    SourceRefreshResult
    | PackageDetailResult
    | UpdateChecksResult
    | InstallReviewResult
    | InstalledPackageResult
    | UpdateReviewResult
    | UpdatedPackageResult
    | RemoveReviewResult
    | RemovedPackageResult
    | TrustReviewResult
    | TrustGrantResult
    | TrustRevokeResult,
    Field(discriminator="type"),
]


RESULT_TYPE_BY_KIND = MappingProxyType({
    "refresh": "source_refresh",
    "inspect": "package_detail",
    "update_check": "update_checks",
    "install_prepare": "install_review",
    "update_prepare": "update_review",
    "remove_prepare": "remove_review",
    "trust_prepare": "trust_review",
    "install_confirm": "installed_package",
    "update_confirm": "updated_package",
    "remove_confirm": "removed_package",
    "trust_confirm": "trust_grant",
    "trust_revoke": "trust_revoke",
})

SUBJECT_TYPES_BY_KIND = MappingProxyType({
    "refresh": frozenset({"source"}),
    "inspect": frozenset({"package"}),
    "update_check": frozenset({"package", "all_packages"}),
    "install_prepare": frozenset({"package", "direct_install"}),
    "update_prepare": frozenset({"package"}),
    "remove_prepare": frozenset({"package"}),
    "trust_prepare": frozenset({"package"}),
    "install_confirm": frozenset({"package"}),
    "update_confirm": frozenset({"package"}),
    "remove_confirm": frozenset({"package"}),
    "trust_confirm": frozenset({"package"}),
    "trust_revoke": frozenset({"package"}),
})


def require_admission_correlations(
    request_id, registry_epoch, kind, subject, selection
):
    request_match = _REQUEST_ID.fullmatch(request_id)
    if request_match is None or request_match.group(1) != registry_epoch:
        raise ValueError("operation request epoch is inconsistent")
    if subject.type not in SUBJECT_TYPES_BY_KIND[kind]:
        raise ValueError("operation kind/subject mismatch")
    trust_kind = kind in {"trust_prepare", "trust_confirm", "trust_revoke"}
    if trust_kind != (selection is not None):
        raise ValueError("operation kind/selection mismatch")


RUNNING_PHASES_BY_KIND = MappingProxyType({
    "refresh": frozenset({"running", "fetching"}),
    "inspect": frozenset({"running", "fetching", "reviewing"}),
    "update_check": frozenset({"running", "fetching", "reviewing"}),
    "install_prepare": frozenset({"running", "fetching", "reviewing"}),
    "update_prepare": frozenset({"running", "fetching", "reviewing"}),
    "remove_prepare": frozenset({"running", "reviewing"}),
    "trust_prepare": frozenset({"running", "reviewing"}),
    "install_confirm": frozenset({"running", "validating", "committing", "recovering"}),
    "update_confirm": frozenset({"running", "validating", "committing", "recovering"}),
    "remove_confirm": frozenset({"running", "validating", "committing", "recovering"}),
    "trust_confirm": frozenset({"running", "validating", "committing"}),
    "trust_revoke": frozenset({"running", "validating", "committing"}),
})


def require_result_kind(kind: str, result: object) -> None:
    """Reject a result that cannot be published for ``kind``."""

    expected = RESULT_TYPE_BY_KIND.get(kind)
    if expected is None or getattr(result, "type", None) != expected:
        raise ValueError("operation kind/result mismatch")


class LifecyclePublicError(StrictLifecycleModel):
    code: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    message: Literal["Workflow marketplace operation failed."] = (
        "Workflow marketplace operation failed."
    )


_READ_ONLY_KINDS = frozenset({
    "inspect",
    "update_check",
    "install_prepare",
    "update_prepare",
    "remove_prepare",
    "trust_prepare",
})
_MUTATION_KINDS = frozenset({
    "install_confirm",
    "update_confirm",
    "remove_confirm",
    "trust_confirm",
    "trust_revoke",
})
_RECOVERY_REASON_BY_ERROR_CODE = MappingProxyType({
    "transaction_rollback_failed": "rollback_failed",
    "transaction_recovery_ambiguous": "recovery_ambiguous",
})


class LifecycleOperation(StrictLifecycleModel):
    schema_version: Literal[2]
    id: str = Field(pattern=_OPERATION_ID_PATTERN)
    registry_epoch: str = Field(pattern=_EPOCH_PATTERN)
    request_id: str = Field(min_length=85, max_length=85, pattern=_REQUEST_ID.pattern)
    kind: LifecycleKind
    subject: LifecycleSubject
    selection: TrustSelection | None
    profile: str = Field(min_length=1, max_length=256)
    state: LifecycleState
    phase: LifecyclePhase
    progress: StrictInt = Field(ge=0, le=100)
    created_at: str = Field(min_length=20, max_length=64)
    started_at: str | None = Field(default=None, min_length=20, max_length=64)
    updated_at: str = Field(min_length=20, max_length=64)
    finished_at: str | None = Field(default=None, min_length=20, max_length=64)
    result: LifecycleResult | None
    error: LifecyclePublicError | None
    outcome: LifecycleOutcome | None

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        return _require_clean_text(value, label="profile")

    @field_validator("created_at", "started_at", "updated_at", "finished_at")
    @classmethod
    def validate_timestamp(cls, value: str | None) -> str | None:
        return _canonical_utc(value, label="operation timestamp")

    @model_validator(mode="after")
    def validate_relationships(self) -> "LifecycleOperation":
        require_admission_correlations(
            self.request_id,
            self.registry_epoch,
            self.kind,
            self.subject,
            self.selection,
        )
        self._validate_time_and_state()
        if self.result is not None:
            require_result_kind(self.kind, self.result)
            self._validate_result_subject()
        self._validate_outcome()
        return self

    def _validate_time_and_state(self) -> None:
        created = _parse_timestamp(self.created_at)
        updated = _parse_timestamp(self.updated_at)
        started = _parse_timestamp(self.started_at) if self.started_at else None
        finished = _parse_timestamp(self.finished_at) if self.finished_at else None
        if updated < created or (started is not None and started < created):
            raise ValueError("operation timestamps are not chronological")
        if started is not None and updated < started:
            raise ValueError("operation timestamps are not chronological")
        if finished is not None and (
            finished < created
            or (started is not None and finished < started)
            or updated < finished
        ):
            raise ValueError("operation timestamps are not chronological")
        if self.state == "pending":
            valid = (
                self.phase == "queued"
                and self.progress == 0
                and started is None
                and finished is None
                and self.result is None
                and self.error is None
                and self.outcome is None
            )
        elif self.state == "running":
            valid = (
                self.phase in RUNNING_PHASES_BY_KIND[self.kind]
                and self.progress < 100
                and started is not None
                and finished is None
                and self.result is None
                and self.error is None
                and self.outcome is None
            )
        elif self.state == "succeeded":
            valid = (
                self.phase == "completed"
                and self.progress == 100
                and started is not None
                and finished is not None
                and self.result is not None
                and self.error is None
                and self.outcome is not None
            )
        elif self.state == "failed":
            valid = (
                self.phase == "failed"
                and self.progress < 100
                and finished is not None
                and self.result is None
                and self.error is not None
                and self.outcome is not None
            )
        else:
            valid = (
                self.phase == "cancelled"
                and self.progress < 100
                and finished is not None
                and self.result is None
                and self.error is None
                and isinstance(self.outcome, CancelledBeforeCommitOutcome)
            )
        if not valid:
            raise ValueError("operation state projection is inconsistent")

    def _validate_result_subject(self) -> None:
        assert self.result is not None
        if isinstance(self.subject, SourceSubject):
            if not isinstance(self.result, SourceRefreshResult) or (
                self.result.value.source_name != self.subject.source_name
            ):
                raise ValueError("refresh result source is inconsistent")
            return
        if isinstance(self.subject, AllPackagesSubject):
            assert isinstance(self.result, UpdateChecksResult)
            identities = [
                _identity_from_v1(item.identity) for item in self.result.value.checks
            ]
            if len(identities) != len(set(identities)):
                raise ValueError("all-package update checks must be unique")
            return
        if isinstance(self.subject, DirectInstallSubject):
            assert isinstance(self.result, InstallReviewResult)
            if self.result.value.identity.source_key != self.subject.source_key:
                raise ValueError("direct review source is inconsistent")
            return
        identity = self.subject.identity
        result_identity = _result_identity(self.result)
        if result_identity != identity:
            raise ValueError("operation result identity is inconsistent")
        if isinstance(self.result, UpdateChecksResult):
            checks = self.result.value.checks
            if len(checks) != 1 or _identity_from_v1(checks[0].identity) != identity:
                raise ValueError("package update check identity is inconsistent")
        if isinstance(self.result, TrustReviewResult):
            assert self.selection is not None
            inventory = {
                (item.workflow_name, item.definition_path)
                for item in self.result.value.package_workflows
            }
            reviewed = {
                (item.workflow_name, item.definition_path)
                for item in self.result.value.workflows
            }
            if isinstance(self.selection, AllTrustSelection):
                if reviewed != inventory:
                    raise ValueError("all-workflow trust review is incomplete")
            else:
                selected = {
                    item
                    for item in inventory
                    if item[0] == self.selection.workflow_name
                }
                if reviewed != selected or len(selected) != 1:
                    raise ValueError(
                        "one-workflow trust review selection is inconsistent"
                    )
        if isinstance(self.result, (TrustGrantResult, TrustRevokeResult)):
            if self.result.value.selection != self.selection:
                raise ValueError("trust result selection is inconsistent")

    def _validate_outcome(self) -> None:
        if self.state in {"pending", "running"}:
            return
        assert self.outcome is not None
        package_state = getattr(self.outcome, "package_state", None)
        if (
            isinstance(self.outcome, KnownUnchangedOutcome)
            and self.outcome.evidence == "rollback_verified"
        ):
            if package_state is None or (
                package_state.state not in {"installed", "absent"}
                or package_state.recovery != "clear"
            ):
                raise ValueError(
                    "verified rollback requires verified current package state"
                )
        if package_state is not None:
            if (
                not isinstance(self.subject, PackageSubject)
                or package_state.identity != self.subject.identity
                or package_state.profile != self.profile
            ):
                raise ValueError("operation outcome package state is inconsistent")
        if self.state == "failed":
            assert self.error is not None
            recovery_reason = _RECOVERY_REASON_BY_ERROR_CODE.get(self.error.code)
            if recovery_reason is not None and not (
                isinstance(self.outcome, RecoveryRequiredOutcome)
                and self.outcome.reason == recovery_reason
            ):
                raise ValueError("recovery failure requires its exact recovery outcome")
            if isinstance(
                self.outcome, (CommittedOutcome, CancelledBeforeCommitOutcome)
            ):
                raise ValueError("failed operation outcome is inconsistent")
            return
        if self.state == "cancelled":
            return
        if self.kind in _READ_ONLY_KINDS:
            if not (
                isinstance(self.outcome, KnownUnchangedOutcome)
                and self.outcome.evidence == "read_only"
                and self.outcome.package_state is None
            ):
                raise ValueError("read-only success outcome is inconsistent")
            return
        if self.kind == "refresh":
            assert isinstance(self.result, SourceRefreshResult)
            if self.result.value.state == "fresh":
                valid = isinstance(self.outcome, CommittedOutcome)
            else:
                valid = isinstance(self.outcome, KnownUnchangedOutcome)
            if not valid or self.outcome.package_state is not None:
                raise ValueError("refresh success outcome is inconsistent")
            return
        assert self.kind in _MUTATION_KINDS
        if not isinstance(self.outcome, CommittedOutcome):
            raise ValueError("successful mutation must have committed outcome")
        package_state = self.outcome.package_state
        if package_state is None or not isinstance(self.subject, PackageSubject):
            raise ValueError("successful mutation requires verified package state")
        if package_state.identity != self.subject.identity:
            raise ValueError("successful mutation package state is inconsistent")
        assert self.result is not None
        if isinstance(self.result, RemovedPackageResult):
            if package_state.state != "absent":
                raise ValueError("removal success requires verified absence")
            return
        if package_state.state != "installed" or package_state.installed is None:
            raise ValueError("mutation success requires verified installed state")
        if isinstance(self.result, (InstalledPackageResult, UpdatedPackageResult)):
            if self.result.value != package_state.installed:
                raise ValueError("mutation result and installed state differ")
        elif isinstance(self.result, (TrustGrantResult, TrustRevokeResult)):
            if package_state.trust is None:
                raise ValueError("trust mutation requires authoritative trust state")
            value = self.result.value
            if (
                value.identity != package_state.trust.identity
                or value.distribution_digest != package_state.trust.distribution_digest
                or value.workflows != package_state.trust.workflows
            ):
                raise ValueError("trust result and package state differ")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _identity_from_v1(value: object) -> PackageIdentity:
    return PackageIdentity.model_validate(
        TypeAdapter(type(value)).dump_python(value, mode="json", by_alias=False)
    )


def _result_identity(result: LifecycleResult) -> PackageIdentity | None:
    if isinstance(result, SourceRefreshResult):
        return None
    if isinstance(result, UpdateChecksResult):
        if len(result.value.checks) != 1:
            return None
        return _identity_from_v1(result.value.checks[0].identity)
    if isinstance(
        result,
        (
            PackageDetailResult,
            InstalledPackageResult,
            UpdatedPackageResult,
            RemovedPackageResult,
        ),
    ):
        return _identity_from_v1(result.value.identity)
    return result.value.identity


__all__ = [
    "LifecycleKind",
    "LifecycleOperation",
    "LifecycleOutcome",
    "LifecyclePhase",
    "LifecycleResult",
    "LifecycleState",
    "LifecycleSubject",
    "PackageIdentity",
    "PackageState",
    "RESULT_TYPE_BY_KIND",
    "RUNNING_PHASES_BY_KIND",
    "SUBJECT_TYPES_BY_KIND",
    "TrustSelection",
    "TrustSnapshot",
    "TrustWorkflowState",
    "require_result_kind",
]
