"""Profile-local workflow marketplace sources and verified catalog state."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
from typing import Callable, Literal, NoReturn
import unicodedata
import urllib.parse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from hermes_cli.git_source import (
    GitSourceError,
    canonical_git_source,
    git_text_contains_credentials,
    resolve_git_source,
    scrub_git_url,
    validate_credential_free_git_source,
)
from hermes_constants import get_hermes_home
from plugins.workflow.locks import WorkflowLockTimeout, workflow_lock
from utils import _is_reparse_point, atomic_write_text

from .models import (
    WorkflowMarketplaceSource,
    WorkflowPackageIndex,
    WorkflowPackageIndexEntry,
)
from .package import WorkflowMarketplaceError


_SOURCE_STATE_VERSION = 1
_CATALOG_STATE_VERSION = 1
_MAX_SOURCES = 128
_MAX_SOURCE_STATE_BYTES = 1024 * 1024
_MAX_CATALOG_STATE_BYTES = 64 * 1024 * 1024
_MAX_ERROR_BYTES = 4096
_SOURCE_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$")
_CONTROL_OR_SPACE_RUN = re.compile(r"[\x00-\x20\x7f\ufeff]+")
_PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
_PATH_DECODE_MAX = 8
_GENERIC_REFRESH_FAILURE = "workflow marketplace source refresh failed"
_DIAGNOSTIC_CREDENTIAL_NAMES = frozenset({
    "accesstoken",
    "apikey",
    "auth",
    "authorization",
    "clientsecret",
    "confirmationtoken",
    "credential",
    "credentials",
    "password",
    "refreshtoken",
    "token",
})
_DIAGNOSTIC_TOKEN_BREAKS = frozenset("<>\"'")
_DIAGNOSTIC_PATH_BREAKS = frozenset("\"'<>()[\\]{},;?#=&/")
_DIAGNOSTIC_TRAILING_URL_PUNCTUATION = frozenset(".,;:!?)]}")
_DIAGNOSTIC_URI_ASCII = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~!$&()*+,;=:@/?#%"
)
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

RefreshState = Literal[
    "fresh",
    "stale",
    "disabled",
    "authentication-failed",
    "malformed",
    "incompatible",
    "unavailable",
    "cancelled",
]


def _canonical_diagnostic_line(value: str) -> str:
    return _CONTROL_OR_SPACE_RUN.sub(" ", value).strip()


def _diagnostic_contains_control(value: str) -> bool:
    return any(
        ord(character) < 32 or ord(character) in {127, 0xFEFF} for character in value
    )


def _diagnostic_matches_ascii_literal(value: str, index: int, literal: str) -> bool:
    if index + len(literal) > len(value):
        return False
    return all(
        character == expected
        or ("a" <= expected <= "z" and character == expected.upper())
        for character, expected in zip(
            value[index : index + len(literal)],
            literal,
            strict=True,
        )
    )


def _diagnostic_identity_has_boundary(value: str, index: int) -> bool:
    if index == 0:
        return True
    previous = value[index - 1]
    return previous != "/" and not previous.isalnum()


def _diagnostic_contains_credential_assignment(value: str) -> bool:
    """Recognize credential-name assignments with one advancing token scan."""

    index = 0
    while index < len(value):
        character = value[index]
        if not (character.isascii() and (character.isalnum() or character in "_-")):
            index += 1
            continue
        start = index
        while index < len(value):
            character = value[index]
            if not (character.isascii() and (character.isalnum() or character in "_-")):
                break
            index += 1
        compact = value[start:index].replace("_", "").replace("-", "").casefold()
        cursor = index
        while cursor < len(value) and value[cursor].isspace():
            cursor += 1
        if (
            compact in _DIAGNOSTIC_CREDENTIAL_NAMES
            and cursor < len(value)
            and value[cursor] in "=:"
        ):
            return True
    return False


def _diagnostic_ipv4_is_safe(value: str) -> bool:
    components = value.split(".")
    return len(components) == 4 and all(
        component
        and component.isascii()
        and component.isdigit()
        and 0 <= int(component) <= 255
        for component in components
    )


def _diagnostic_ipv6_is_safe(value: str) -> bool:
    if not value or "%" in value or value.count("::") > 1:
        return False

    compressed = "::" in value
    if compressed:
        left, right = value.split("::")
    else:
        left, right = value, ""
    left_parts = [] if not left else left.split(":")
    right_parts = [] if not right else right.split(":")
    parts = [*left_parts, *right_parts]
    if any(not part for part in parts):
        return False

    slots = 0
    for index, part in enumerate(parts):
        if "." in part:
            if index != len(parts) - 1 or not _diagnostic_ipv4_is_safe(part):
                return False
            slots += 2
        else:
            if not 1 <= len(part) <= 4 or any(
                character not in _HEX_DIGITS for character in part
            ):
                return False
            slots += 1
    return slots < 8 if compressed else slots == 8


def _diagnostic_hostname_is_safe(value: str) -> bool:
    if not value or len(value) > 253:
        return False
    if all(
        character.isascii() and (character.isdigit() or character == ".")
        for character in value
    ):
        return _diagnostic_ipv4_is_safe(value)
    hostname = value[:-1] if value.endswith(".") else value
    if not hostname:
        return False
    for label in hostname.split("."):
        if (
            not 1 <= len(label) <= 63
            or label.startswith("-")
            or label.endswith("-")
            or any(not (character.isalnum() or character == "-") for character in label)
        ):
            return False
    return True


def _diagnostic_port_is_safe(value: str) -> bool:
    return bool(value) and value.isascii() and value.isdigit() and int(value) <= 65535


def _diagnostic_http_authority_is_safe(value: str) -> bool:
    """Validate a credential-free DNS, IPv4, or bracketed IPv6 authority."""

    if not value or "@" in value:
        return False
    if value.startswith("["):
        closing = value.find("]", 1)
        if closing <= 1 or "[" in value[1:] or "]" in value[closing + 1 :]:
            return False
        address = value[1:closing]
        suffix = value[closing + 1 :]
        return _diagnostic_ipv6_is_safe(address) and (
            not suffix
            or (suffix.startswith(":") and _diagnostic_port_is_safe(suffix[1:]))
        )

    if value.count(":") > 1:
        return False
    host, separator, port = value.rpartition(":")
    if not separator:
        host = value
    elif not _diagnostic_port_is_safe(port):
        return False
    return _diagnostic_hostname_is_safe(host)


def _diagnostic_path_character_is_safe(character: str) -> bool:
    return not character.isspace() and character not in _DIAGNOSTIC_PATH_BREAKS


def _diagnostic_staging_identity_starts_at(
    value: str,
    index: int,
    *,
    inside_http_url: bool,
) -> bool:
    if value[index] != ".":
        return False
    length = 0
    for candidate in (".quarantine", ".staging"):
        if _diagnostic_matches_ascii_literal(value, index, candidate):
            length = len(candidate)
            break
    if not length:
        return False
    if inside_http_url and index > 0 and value[index - 1] == "/":
        return False
    end = index + length
    return (
        end == len(value)
        or value[end] == "/"
        or not (value[end].isalnum() or value[end] in "._-")
    )


def _diagnostic_local_identity_starts_at(
    value: str,
    index: int,
    *,
    inside_http_url: bool = False,
) -> bool:
    if _diagnostic_staging_identity_starts_at(
        value,
        index,
        inside_http_url=inside_http_url,
    ):
        return True
    if inside_http_url:
        has_boundary = index == 0 or (
            value[index - 1] not in "/-._~%" and not value[index - 1].isalnum()
        )
    else:
        has_boundary = _diagnostic_identity_has_boundary(value, index)
    if not has_boundary:
        return False
    if value[index : index + len("file:")].casefold() == "file:":
        cursor = index + len("file:")
        return cursor < len(value) and not value[cursor].isspace()
    if (
        index + 2 < len(value)
        and value[index].isascii()
        and value[index].isalpha()
        and value[index + 1] == ":"
        and value[index + 2] == "/"
    ):
        return True
    if value[index] != "/" or index + 1 >= len(value):
        return False
    cursor = index
    while cursor < len(value) and value[cursor] == "/":
        cursor += 1
    return cursor < len(value) and _diagnostic_path_character_is_safe(value[cursor])


def _diagnostic_http_scheme_length_at(value: str, index: int) -> int:
    if value[index] not in "hH":
        return 0
    if _diagnostic_matches_ascii_literal(value, index, "https://"):
        return len("https://")
    if _diagnostic_matches_ascii_literal(value, index, "http://"):
        return len("http://")
    return 0


def _diagnostic_http_scheme_has_boundary(value: str, index: int) -> bool:
    if index == 0:
        return True
    previous = value[index - 1]
    return previous != "/" and not previous.isalnum()


def _diagnostic_http_token_end(value: str, scheme_end: int) -> int:
    token_end = scheme_end
    while token_end < len(value):
        character = value[token_end]
        if character.isspace() or character in _DIAGNOSTIC_TOKEN_BREAKS:
            break
        if _diagnostic_http_scheme_length_at(
            value, token_end
        ) and _diagnostic_http_scheme_has_boundary(value, token_end):
            break
        token_end += 1
    return token_end


def _diagnostic_trim_url_punctuation(
    value: str,
    scheme_end: int,
    token_end: int,
) -> int:
    end = token_end
    authority_terminated = any(
        value[index] in "/?#" for index in range(scheme_end, token_end)
    )
    while end > scheme_end and value[end - 1] in _DIAGNOSTIC_TRAILING_URL_PUNCTUATION:
        if value[end - 1] == ":" and not authority_terminated:
            break
        if value[end - 1] == "]" and value[scheme_end] == "[":
            ipv6_closing = value.find("]", scheme_end + 1, end)
            if ipv6_closing == end - 1:
                break
        end -= 1
    return end


def _diagnostic_http_url_end(
    original: str,
    normalized: str,
    start: int,
    scheme_length: int,
) -> int | None:
    """Return one proven-safe URL span end, or ``None`` for an unsafe token."""

    scheme_end = start + scheme_length
    if original[start:scheme_end] != normalized[start:scheme_end]:
        return None
    token_end = _diagnostic_http_token_end(normalized, scheme_end)
    end = _diagnostic_trim_url_punctuation(normalized, scheme_end, token_end)
    if end <= scheme_end:
        return None

    authority_end = scheme_end
    while authority_end < end and normalized[authority_end] not in "/?#":
        authority_end += 1
    if not _diagnostic_http_authority_is_safe(normalized[scheme_end:authority_end]):
        return None

    first_path_slash = (
        authority_end
        if authority_end < end and normalized[authority_end] == "/"
        else -1
    )
    fragment_seen = False
    index = authority_end
    while index < end:
        character = normalized[index]
        if original[index] == "\\":
            return None
        if _diagnostic_http_scheme_length_at(normalized, index):
            return None
        if index != first_path_slash and _diagnostic_local_identity_starts_at(
            normalized,
            index,
            inside_http_url=True,
        ):
            return None
        if character == "#":
            if fragment_seen:
                return None
            fragment_seen = True
        if character == "%":
            encoded = bytearray()
            while index < end and normalized[index] == "%":
                if (
                    index + 2 >= end
                    or normalized[index + 1] not in _HEX_DIGITS
                    or normalized[index + 2] not in _HEX_DIGITS
                ):
                    return None
                encoded.append(int(normalized[index + 1 : index + 3], 16))
                index += 3
            try:
                decoded = encoded.decode("utf-8")
            except UnicodeDecodeError:
                return None
            if any(
                _diagnostic_contains_control(character) or character in "/\\%"
                for character in decoded
            ):
                return None
            continue
        if character.isascii():
            if character not in _DIAGNOSTIC_URI_ASCII:
                return None
        elif character.isspace():
            return None
        index += 1
    return end


def _scan_diagnostic_text(value: str) -> tuple[bool, bool]:
    """Return ``(unsafe, unprotected_percent_escape)`` in linear time."""

    normalized = value.replace("\\", "/")
    unprotected_percent_escape = False
    index = 0
    while index < len(normalized):
        scheme_length = _diagnostic_http_scheme_length_at(normalized, index)
        if scheme_length:
            end = _diagnostic_http_url_end(value, normalized, index, scheme_length)
            if end is None:
                return True, unprotected_percent_escape
            index = end
            continue
        if _diagnostic_local_identity_starts_at(normalized, index):
            return True, unprotected_percent_escape
        if (
            normalized[index] == "%"
            and index + 2 < len(normalized)
            and normalized[index + 1] in _HEX_DIGITS
            and normalized[index + 2] in _HEX_DIGITS
        ):
            unprotected_percent_escape = True
        index += 1
    return (
        _diagnostic_contains_credential_assignment(value),
        unprotected_percent_escape,
    )


def _diagnostic_text_is_unsafe(value: str) -> bool:
    return _scan_diagnostic_text(value)[0]


def redact_source_refresh_message(value: str, repository_url: str | None = None) -> str:
    """Return one bounded diagnostic with credentials and private paths removed."""

    if not isinstance(value, str) or (
        repository_url is not None and not isinstance(repository_url, str)
    ):
        return _GENERIC_REFRESH_FAILURE

    redacted = _canonical_diagnostic_line(value)[:_MAX_ERROR_BYTES]
    if _diagnostic_text_is_unsafe(redacted):
        return _GENERIC_REFRESH_FAILURE

    probe = redacted
    decoded_layers = [redacted]
    for _ in range(_PATH_DECODE_MAX):
        if _PERCENT_ESCAPE.search(probe) is None:
            break
        try:
            decoded = urllib.parse.unquote(probe, errors="strict")
        except UnicodeDecodeError:
            return _GENERIC_REFRESH_FAILURE
        if decoded == probe:
            break
        if _diagnostic_contains_control(decoded) or _diagnostic_text_is_unsafe(decoded):
            return _GENERIC_REFRESH_FAILURE
        probe = decoded
        decoded_layers.append(decoded)
    else:
        if (
            _PERCENT_ESCAPE.search(probe) is not None
            and _scan_diagnostic_text(probe)[1]
        ):
            return _GENERIC_REFRESH_FAILURE

    if git_text_contains_credentials("\n".join(decoded_layers)):
        return _GENERIC_REFRESH_FAILURE

    if repository_url:
        try:
            scrubbed_repository_url = scrub_git_url(repository_url)
        except ValueError:
            return _GENERIC_REFRESH_FAILURE
        redacted = redacted.replace(repository_url, scrubbed_repository_url)
    return redacted


def source_refresh_message_is_safe(value: str) -> bool:
    """Return whether a diagnostic is already in its canonical persisted form."""

    return redact_source_refresh_message(value) == value


class _StateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _SourceState(_StateModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    sources: list[WorkflowMarketplaceSource] = Field(max_length=_MAX_SOURCES)

    @model_validator(mode="after")
    def require_unique_sorted_sources(self) -> "_SourceState":
        names = [source.name for source in self.sources]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("sources must be unique and sorted by name")
        return self


class VerifiedSourceCatalog(_StateModel):
    """One independently verified repository index at an exact commit."""

    source: WorkflowMarketplaceSource
    resolved_commit: str = Field(alias="resolvedCommit", pattern=r"^[0-9a-f]{40}$")
    verified_at: str = Field(alias="verifiedAt", min_length=20, max_length=64)
    packages: list[WorkflowPackageIndexEntry] = Field(max_length=4096)

    @model_validator(mode="after")
    def require_valid_index_projection(self) -> "VerifiedSourceCatalog":
        WorkflowPackageIndex.model_validate({
            "schemaVersion": 1,
            "packages": [
                package.model_dump(mode="json", by_alias=True)
                for package in self.packages
            ],
        })
        return self

    @field_validator("verified_at")
    @classmethod
    def require_canonical_timestamp(cls, value: str) -> str:
        return _canonical_timestamp(value)


class SourceRefreshStatus(_StateModel):
    """Credential-free refresh status retained separately from verified bytes."""

    source_name: str = Field(
        alias="sourceName", min_length=1, max_length=64, pattern=_SOURCE_NAME.pattern
    )
    state: RefreshState
    attempted_at: str = Field(alias="attemptedAt", min_length=20, max_length=64)
    diagnostic_code: str | None = Field(
        default=None, alias="diagnosticCode", max_length=128
    )
    message: str | None = Field(default=None, max_length=_MAX_ERROR_BYTES)

    @field_validator("attempted_at")
    @classmethod
    def require_canonical_timestamp(cls, value: str) -> str:
        return _canonical_timestamp(value)

    @field_validator("message")
    @classmethod
    def require_redacted_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not source_refresh_message_is_safe(value):
            raise ValueError("refresh status message must be canonically redacted")
        return value

    @model_validator(mode="after")
    def require_legal_diagnostic_shape(self) -> "SourceRefreshStatus":
        if self.state == "fresh":
            if self.diagnostic_code is not None or self.message is not None:
                raise ValueError("fresh status must not contain a diagnostic")
        elif self.state in {"disabled", "cancelled"}:
            raise ValueError("ephemeral refresh states must not be persisted")
        elif self.diagnostic_code is None or self.message is None:
            raise ValueError("failed refresh status requires a diagnostic")
        return self


class _CatalogState(_StateModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    verified: list[VerifiedSourceCatalog] = Field(max_length=_MAX_SOURCES)
    statuses: list[SourceRefreshStatus] = Field(max_length=_MAX_SOURCES)

    @model_validator(mode="after")
    def require_unique_sorted_entries(self) -> "_CatalogState":
        verified_names = [item.source.name for item in self.verified]
        status_names = [item.source_name for item in self.statuses]
        if (
            verified_names != sorted(verified_names)
            or len(verified_names) != len(set(verified_names))
            or status_names != sorted(status_names)
            or len(status_names) != len(set(status_names))
        ):
            raise ValueError("catalog entries must be unique and sorted by source")
        verified_names_set = set(verified_names)
        statuses_by_name = {status.source_name: status for status in self.statuses}
        if not verified_names_set.issubset(statuses_by_name):
            raise ValueError("every verified catalog requires refresh status")
        for name, status in statuses_by_name.items():
            if name in verified_names_set:
                if status.state not in {"fresh", "stale"}:
                    raise ValueError("verified catalogs require fresh or stale status")
            elif status.state in {"fresh", "stale"}:
                raise ValueError("fresh or stale status requires a verified catalog")
        return self


def _canonical_timestamp(value: str) -> str:
    if not value.endswith("Z"):
        raise ValueError("timestamp must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as error:
        raise ValueError("timestamp must be canonical UTC") from error
    canonical = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if parsed.tzinfo is None or value != canonical:
        raise ValueError("timestamp must be canonical UTC")
    return value


def _fail(code: str, message: str) -> NoReturn:
    raise WorkflowMarketplaceError(code, message)


def _check_cancelled(cancelled: Callable[[], bool]) -> None:
    try:
        cancellation_requested = cancelled()
    except Exception:
        cancellation_requested = True
    if cancellation_requested:
        _fail("source_cancelled", "workflow marketplace refresh was cancelled")


def _normalize_name(name: str) -> str:
    if not isinstance(name, str):
        _fail("source_name_invalid", "source name must be text")
    normalized = unicodedata.normalize("NFKC", name.strip()).casefold()
    if _SOURCE_NAME.fullmatch(normalized) is None:
        _fail(
            "source_name_invalid",
            "source name must use 1-64 lowercase letters, digits, '_' or '-'",
        )
    return normalized


def _canonical_source(
    name: str,
    repository_url: str,
    *,
    ref: str | None,
    enabled: bool,
) -> WorkflowMarketplaceSource:
    normalized_name = _normalize_name(name)
    raw = {
        "name": normalized_name,
        "repositoryUrl": repository_url,
        "ref": ref,
        "enabled": enabled,
    }
    try:
        WorkflowMarketplaceSource.model_validate(raw)
    except ValidationError as error:
        if "credentials" in str(error).casefold():
            _fail(
                "source_credentials_forbidden",
                "marketplace source URLs must not contain credentials",
            )
        _fail("source_invalid", "marketplace source configuration is invalid")
    try:
        validate_credential_free_git_source(repository_url)
        resolved = resolve_git_source(repository_url)
    except GitSourceError as error:
        if "credentials" in str(error).casefold():
            _fail(
                "source_credentials_forbidden",
                "marketplace source URLs must not contain credentials",
            )
        _fail("source_invalid", "marketplace Git source identity is invalid")
    identity = canonical_git_source(resolved.clone_url, resolved.subdirectory)
    try:
        return WorkflowMarketplaceSource.model_validate({
            **raw,
            "repositoryUrl": identity,
        })
    except (
        ValidationError
    ) as error:  # defensive: the shared canonical form is untrusted
        if "credentials" in str(error).casefold():
            _fail(
                "source_credentials_forbidden",
                "marketplace source URLs must not contain credentials",
            )
        _fail("source_invalid", "marketplace source configuration is invalid")


def _strict_json(raw: bytes, *, code: str) -> object:
    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    try:
        return json.loads(
            raw,
            object_pairs_hook=reject_duplicate,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail(code, "persisted marketplace state is malformed")


def _read_bounded(path: Path, *, limit: int, size_code: str) -> bytes:
    invalid_code = size_code.replace("size_limit", "invalid")
    try:
        path_metadata = path.lstat()
        if stat.S_ISLNK(path_metadata.st_mode) or _is_reparse_point(path_metadata):
            _fail(invalid_code, "state file must not be a symbolic link")
    except FileNotFoundError:
        _fail(invalid_code, "state file disappeared while being read")
    except OSError:
        _fail(invalid_code, "state file is unreadable")
    flags = os.O_RDONLY
    for option in ("O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW", "O_BINARY"):
        flags |= getattr(os, option, 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _fail(invalid_code, "state file is unreadable")
    try:
        metadata = os.fstat(descriptor)
        if _is_reparse_point(metadata) or not stat.S_ISREG(metadata.st_mode):
            _fail(invalid_code, "state is not a file")
        if metadata.st_size > limit:
            _fail(size_code, "persisted marketplace state exceeds its byte limit")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            _fail(size_code, "persisted marketplace state exceeds its byte limit")
        return raw
    finally:
        os.close(descriptor)


def _render_state(
    value: BaseModel,
    *,
    limit: int,
    size_code: str,
) -> str:
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    chunks: list[str] = []
    encoded_size = 0
    for chunk in encoder.iterencode(value.model_dump(mode="json", by_alias=True)):
        encoded_size += len(chunk.encode("utf-8"))
        if encoded_size + 1 > limit:
            _fail(size_code, "persisted marketplace state exceeds its byte limit")
        chunks.append(chunk)
    chunks.append("\n")
    return "".join(chunks)


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _redacted_error(error: WorkflowMarketplaceError, repository_url: str) -> str:
    rendered = redact_source_refresh_message(str(error), repository_url)
    if not rendered or git_text_contains_credentials(rendered):
        rendered = "marketplace source refresh failed"
    encoded = rendered.encode("utf-8")[:_MAX_ERROR_BYTES]
    return encoded.decode("utf-8", errors="ignore")


class WorkflowSourceStore:
    """Own profile-local source definitions and verified catalog cache."""

    max_source_state_bytes = _MAX_SOURCE_STATE_BYTES
    max_catalog_state_bytes = _MAX_CATALOG_STATE_BYTES

    def __init__(self, hermes_home: Path | None = None):
        home = Path(hermes_home) if hermes_home is not None else get_hermes_home()
        self.home = home
        self.root = home / "marketplace" / "workflows"
        self.path = self.root / "sources.json"
        self.catalog_path = self.root / "catalog.json"
        self.lock_path = self.root / "marketplace.lock"

    def _ensure_private_root(self) -> tuple[int, int]:
        root_identity: tuple[int, int] | None = None
        for index, directory in enumerate((self.home, self.root.parent, self.root)):
            directory.mkdir(parents=index == 0, exist_ok=True, mode=0o700)
            try:
                metadata = directory.lstat()
            except OSError:
                _fail("source_state_invalid", "marketplace state directory is invalid")
            if (
                stat.S_ISLNK(metadata.st_mode)
                or _is_reparse_point(metadata)
                or not stat.S_ISDIR(metadata.st_mode)
            ):
                _fail(
                    "source_state_invalid",
                    "marketplace state directory must not be a link",
                )
            if os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o222 == 0:
                _fail(
                    "source_state_invalid",
                    "marketplace state directory is read-only",
                )
            if (
                os.name != "nt"
                and directory != self.home
                and stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                _fail(
                    "source_state_invalid",
                    "marketplace state directory permissions are not private",
                )
            if directory == self.root:
                root_identity = (metadata.st_dev, metadata.st_ino)
        lock_exists = _path_entry_exists(self.lock_path)
        if lock_exists:
            try:
                lock_metadata = self.lock_path.lstat()
            except OSError:
                _fail("source_state_invalid", "marketplace lock file is invalid")
            if (
                stat.S_ISLNK(lock_metadata.st_mode)
                or _is_reparse_point(lock_metadata)
                or not stat.S_ISREG(lock_metadata.st_mode)
                or (os.name != "nt" and stat.S_IMODE(lock_metadata.st_mode) != 0o600)
            ):
                _fail("source_state_invalid", "marketplace lock file is invalid")
        flags = os.O_CREAT | os.O_APPEND
        for option in ("O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW", "O_BINARY"):
            flags |= getattr(os, option, 0)
        try:
            descriptor = os.open(self.lock_path, flags, 0o600)
        except OSError:
            _fail("source_state_invalid", "marketplace lock file is invalid")
        try:
            metadata = os.fstat(descriptor)
            if _is_reparse_point(metadata) or not stat.S_ISREG(metadata.st_mode):
                _fail("source_state_invalid", "marketplace lock is not a regular file")
            if os.name != "nt" and not lock_exists:
                os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        if root_identity is None:
            _fail("source_state_invalid", "marketplace state directory is invalid")
        return root_identity

    @contextmanager
    def _locked(self):
        from .lifecycle_state import _source_scope

        # Protect every refresh read/status fallback, before touching a home.
        _source_scope(store=self)
        root_identity = self._ensure_private_root()
        with workflow_lock(self.lock_path):
            _source_scope(store=self)
            try:
                metadata = self.root.lstat()
            except OSError:
                _fail("source_state_invalid", "marketplace state directory changed")
            if (
                stat.S_ISLNK(metadata.st_mode)
                or _is_reparse_point(metadata)
                or (metadata.st_dev, metadata.st_ino) != root_identity
            ):
                _fail("source_state_invalid", "marketplace state directory changed")
            yield root_identity

    def _read_sources(self) -> _SourceState:
        if not _path_entry_exists(self.path):
            return _SourceState.model_validate({"schemaVersion": 1, "sources": []})
        raw = _read_bounded(
            self.path,
            limit=self.max_source_state_bytes,
            size_code="source_state_size_limit",
        )
        value = _strict_json(raw, code="source_state_invalid")
        try:
            return _SourceState.model_validate(value)
        except ValidationError:
            _fail("source_state_invalid", "persisted source state is invalid")

    def _read_catalog(self) -> _CatalogState:
        if not _path_entry_exists(self.catalog_path):
            return _CatalogState.model_validate({
                "schemaVersion": 1,
                "verified": [],
                "statuses": [],
            })
        raw = _read_bounded(
            self.catalog_path,
            limit=self.max_catalog_state_bytes,
            size_code="catalog_state_size_limit",
        )
        value = _strict_json(raw, code="catalog_state_invalid")
        try:
            return _CatalogState.model_validate(value)
        except ValidationError:
            _fail("catalog_state_invalid", "persisted catalog state is invalid")

    def _write(
        self,
        path: Path,
        value: BaseModel,
        *,
        parent_identity: tuple[int, int],
        cancelled: Callable[[], bool] | None = None,
        verified_publication: VerifiedSourceCatalog | None = None,
    ) -> None:
        if path == self.path:
            limit = self.max_source_state_bytes
            size_code = "source_state_size_limit"
        else:
            limit = self.max_catalog_state_bytes
            size_code = "catalog_state_size_limit"
        rendered = _render_state(value, limit=limit, size_code=size_code)
        if cancelled is not None:
            _check_cancelled(cancelled)
        from .lifecycle_state import _source_scope

        # Encoding/cancellation callbacks can invalidate ownership after locking.
        # Status writes require the same binding as verified-cache publication.
        _source_scope(store=self)
        if verified_publication is not None:
            from .lifecycle_state import _source_write_started

            _source_write_started(self, verified_publication)
        try:
            atomic_write_text(
                path,
                rendered,
                tmp_prefix=f"{path.name}.tmp-",
                create_mode=0o600,
                no_follow=True,
                expected_parent_identity=parent_identity,
            )
        except OSError:
            code = (
                "source_state_write_failed"
                if path == self.path
                else "catalog_state_write_failed"
            )
            _fail(code, "could not atomically replace marketplace state")
        if verified_publication is not None:
            from .lifecycle_state import _source_write_finished

            # The caller still owns the source lock. Require the exact complete
            # replacement and the same private directory before claiming publication.
            metadata = self.root.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or _is_reparse_point(metadata)
                or (metadata.st_dev, metadata.st_ino) != parent_identity
                or self._read_catalog() != value
            ):
                _fail("catalog_state_write_failed", "source publication is unverified")
            _source_write_finished(self, verified_publication)

    def _require_current_source(self, expected: WorkflowMarketplaceSource) -> None:
        current = next(
            (
                source
                for source in self._read_sources().sources
                if source.name == expected.name
            ),
            None,
        )
        if current is None or current != expected or not current.enabled:
            _fail(
                "source_changed",
                "marketplace source changed while its refresh was in progress",
            )

    def add(
        self,
        name: str | WorkflowMarketplaceSource,
        repository_url: str | None = None,
        *,
        ref: str | None = None,
        enabled: bool = True,
    ) -> WorkflowMarketplaceSource:
        if isinstance(name, WorkflowMarketplaceSource):
            if repository_url is not None or ref is not None or enabled is not True:
                _fail("source_invalid", "source object cannot be combined with fields")
            source = _canonical_source(
                name.name,
                name.repository_url,
                ref=name.ref,
                enabled=name.enabled,
            )
        else:
            if repository_url is None:
                _fail("source_invalid", "source repository URL is required")
            source = _canonical_source(
                name,
                repository_url,
                ref=ref,
                enabled=enabled,
            )
        try:
            with self._locked() as parent_identity:
                state = self._read_sources()
                if any(item.name == source.name for item in state.sources):
                    _fail(
                        "source_already_exists",
                        f"marketplace source {source.name!r} already exists",
                    )
                if len(state.sources) >= _MAX_SOURCES:
                    _fail("source_limit", "marketplace source count limit reached")
                sources = sorted([*state.sources, source], key=lambda item: item.name)
                self._write(
                    self.path,
                    _SourceState.model_validate({
                        "schemaVersion": _SOURCE_STATE_VERSION,
                        "sources": sources,
                    }),
                    parent_identity=parent_identity,
                )
        except WorkflowLockTimeout as error:
            _fail("source_lock_timeout", str(error))
        return source

    def list_sources(self) -> tuple[WorkflowMarketplaceSource, ...]:
        if not _path_entry_exists(self.path):
            return ()
        try:
            with self._locked():
                return tuple(self._read_sources().sources)
        except WorkflowLockTimeout as error:
            _fail("source_lock_timeout", str(error))

    def get(self, name: str) -> WorkflowMarketplaceSource:
        normalized = _normalize_name(name)
        for source in self.list_sources():
            if source.name == normalized:
                return source
        _fail("source_not_found", f"marketplace source {normalized!r} was not found")

    def set_enabled(self, name: str, enabled: bool) -> WorkflowMarketplaceSource:
        normalized = _normalize_name(name)
        if not isinstance(enabled, bool):
            _fail("source_invalid", "source enabled state must be boolean")
        try:
            with self._locked() as parent_identity:
                state = self._read_sources()
                existing = next(
                    (source for source in state.sources if source.name == normalized),
                    None,
                )
                if existing is None:
                    _fail(
                        "source_not_found",
                        f"marketplace source {normalized!r} was not found",
                    )
                updated = existing.model_copy(update={"enabled": enabled})
                sources = [
                    updated if source.name == normalized else source
                    for source in state.sources
                ]
                self._write(
                    self.path,
                    _SourceState.model_validate({
                        "schemaVersion": _SOURCE_STATE_VERSION,
                        "sources": sources,
                    }),
                    parent_identity=parent_identity,
                )
                return updated
        except WorkflowLockTimeout as error:
            _fail("source_lock_timeout", str(error))

    def update(
        self,
        name: str,
        repository_url: str,
        *,
        ref: str | None,
        enabled: bool,
    ) -> WorkflowMarketplaceSource:
        """Atomically replace one source configuration under the marketplace lock."""

        normalized = _normalize_name(name)
        updated = _canonical_source(
            normalized,
            repository_url,
            ref=ref,
            enabled=enabled,
        )
        try:
            with self._locked() as parent_identity:
                state = self._read_sources()
                existing = next(
                    (source for source in state.sources if source.name == normalized),
                    None,
                )
                if existing is None:
                    _fail(
                        "source_not_found",
                        f"marketplace source {normalized!r} was not found",
                    )
                identity_changed = (
                    existing.repository_url != updated.repository_url
                    or existing.ref != updated.ref
                )
                if identity_changed:
                    catalog = self._read_catalog()
                    self._write(
                        self.catalog_path,
                        _CatalogState.model_validate({
                            "schemaVersion": _CATALOG_STATE_VERSION,
                            "verified": [
                                item
                                for item in catalog.verified
                                if item.source.name != normalized
                            ],
                            "statuses": [
                                item
                                for item in catalog.statuses
                                if item.source_name != normalized
                            ],
                        }),
                        parent_identity=parent_identity,
                    )
                self._write(
                    self.path,
                    _SourceState.model_validate({
                        "schemaVersion": _SOURCE_STATE_VERSION,
                        "sources": [
                            updated if source.name == normalized else source
                            for source in state.sources
                        ],
                    }),
                    parent_identity=parent_identity,
                )
                return updated
        except WorkflowLockTimeout as error:
            _fail("source_lock_timeout", str(error))

    def remove(self, name: str) -> WorkflowMarketplaceSource:
        normalized = _normalize_name(name)
        try:
            with self._locked() as parent_identity:
                state = self._read_sources()
                catalog = self._read_catalog()
                removed = next(
                    (source for source in state.sources if source.name == normalized),
                    None,
                )
                if removed is None:
                    _fail(
                        "source_not_found",
                        f"marketplace source {normalized!r} was not found",
                    )
                self._write(
                    self.catalog_path,
                    _CatalogState.model_validate({
                        "schemaVersion": _CATALOG_STATE_VERSION,
                        "verified": [
                            item
                            for item in catalog.verified
                            if item.source.name != normalized
                        ],
                        "statuses": [
                            item
                            for item in catalog.statuses
                            if item.source_name != normalized
                        ],
                    }),
                    parent_identity=parent_identity,
                )
                self._write(
                    self.path,
                    _SourceState.model_validate({
                        "schemaVersion": _SOURCE_STATE_VERSION,
                        "sources": [
                            source
                            for source in state.sources
                            if source.name != normalized
                        ],
                    }),
                    parent_identity=parent_identity,
                )
                return removed
        except WorkflowLockTimeout as error:
            _fail("source_lock_timeout", str(error))

    def cached(self, source: WorkflowMarketplaceSource) -> VerifiedSourceCatalog | None:
        if not _path_entry_exists(self.catalog_path):
            return None
        try:
            with self._locked():
                for cached in self._read_catalog().verified:
                    if (
                        cached.source.name == source.name
                        and cached.source.repository_url == source.repository_url
                        and cached.source.ref == source.ref
                    ):
                        return cached
        except WorkflowLockTimeout as error:
            _fail("source_lock_timeout", str(error))
        return None

    def status(self, name: str) -> SourceRefreshStatus | None:
        normalized = _normalize_name(name)
        if not _path_entry_exists(self.catalog_path):
            return None
        try:
            with self._locked():
                return next(
                    (
                        status
                        for status in self._read_catalog().statuses
                        if status.source_name == normalized
                    ),
                    None,
                )
        except WorkflowLockTimeout as error:
            _fail("source_lock_timeout", str(error))

    def verified_catalogs(self) -> tuple[VerifiedSourceCatalog, ...]:
        if not _path_entry_exists(self.catalog_path):
            return ()
        try:
            with self._locked():
                return tuple(self._read_catalog().verified)
        except WorkflowLockTimeout as error:
            _fail("source_lock_timeout", str(error))

    def snapshot(
        self,
    ) -> tuple[
        tuple[WorkflowMarketplaceSource, ...],
        tuple[VerifiedSourceCatalog, ...],
        tuple[SourceRefreshStatus, ...],
    ]:
        """Read source definitions and cache under one consistent lock."""

        if not _path_entry_exists(self.path) and not _path_entry_exists(
            self.catalog_path
        ):
            return (), (), ()
        try:
            with self._locked():
                sources = tuple(self._read_sources().sources)
                catalog = self._read_catalog()
                sources_by_name = {source.name: source for source in sources}
                if any(
                    status.source_name not in sources_by_name
                    for status in catalog.statuses
                ):
                    _fail(
                        "catalog_state_invalid",
                        "catalog status refers to an unknown source",
                    )
                for cached in catalog.verified:
                    source = sources_by_name.get(cached.source.name)
                    if (
                        source is None
                        or source.repository_url != cached.source.repository_url
                        or source.ref != cached.source.ref
                    ):
                        _fail(
                            "catalog_state_invalid",
                            "verified catalog source identity is inconsistent",
                        )
                return sources, tuple(catalog.verified), tuple(catalog.statuses)
        except WorkflowLockTimeout as error:
            _fail("source_lock_timeout", str(error))

    def replace_verified_cache(
        self,
        verified: VerifiedSourceCatalog,
        *,
        attempted_at: str,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> None:
        try:
            with self._locked() as parent_identity:
                self._require_current_source(verified.source)
                catalog = self._read_catalog()
                verified_entries = [
                    item
                    for item in catalog.verified
                    if item.source.name != verified.source.name
                ]
                verified_entries.append(verified)
                statuses = [
                    item
                    for item in catalog.statuses
                    if item.source_name != verified.source.name
                ]
                statuses.append(
                    SourceRefreshStatus.model_validate({
                        "sourceName": verified.source.name,
                        "state": "fresh",
                        "attemptedAt": attempted_at,
                    })
                )
                replacement = _CatalogState.model_validate({
                    "schemaVersion": _CATALOG_STATE_VERSION,
                    "verified": sorted(
                        verified_entries, key=lambda item: item.source.name
                    ),
                    "statuses": sorted(statuses, key=lambda item: item.source_name),
                })
                _check_cancelled(cancelled)
                self._write(
                    self.catalog_path,
                    replacement,
                    parent_identity=parent_identity,
                    cancelled=cancelled,
                    verified_publication=verified,
                )
        except WorkflowLockTimeout as error:
            _fail("source_lock_timeout", str(error))

    def record_failed_refresh(
        self,
        source: WorkflowMarketplaceSource,
        error: WorkflowMarketplaceError,
        *,
        attempted_at: str,
        state: RefreshState,
    ) -> VerifiedSourceCatalog | None:
        message = _redacted_error(error, source.repository_url)
        try:
            with self._locked() as parent_identity:
                self._require_current_source(source)
                catalog = self._read_catalog()
                cached = next(
                    (
                        item
                        for item in catalog.verified
                        if item.source.name == source.name
                        and item.source.repository_url == source.repository_url
                        and item.source.ref == source.ref
                    ),
                    None,
                )
                statuses = [
                    item for item in catalog.statuses if item.source_name != source.name
                ]
                statuses.append(
                    SourceRefreshStatus.model_validate({
                        "sourceName": source.name,
                        "state": "stale" if cached is not None else state,
                        "attemptedAt": attempted_at,
                        "diagnosticCode": error.code,
                        "message": message,
                    })
                )
                self._write(
                    self.catalog_path,
                    _CatalogState.model_validate({
                        "schemaVersion": _CATALOG_STATE_VERSION,
                        "verified": catalog.verified,
                        "statuses": sorted(statuses, key=lambda item: item.source_name),
                    }),
                    parent_identity=parent_identity,
                )
                return cached
        except WorkflowLockTimeout as lock_error:
            _fail("source_lock_timeout", str(lock_error))


__all__ = [
    "RefreshState",
    "SourceRefreshStatus",
    "VerifiedSourceCatalog",
    "WorkflowSourceStore",
]
