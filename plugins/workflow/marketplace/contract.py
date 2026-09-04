"""Canonical workflow-package contract generation and artifact loading."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from .models import (
    CompatibilityRules,
    DigestRules,
    PathRules,
    ResourceRules,
    WorkflowPackageContract,
    WorkflowPackageDigests,
    WorkflowPackageIndex,
    WorkflowPackageManifest,
)


CONTRACT_VERSION = 1
CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "contracts"
CONTRACT_PATH = CONTRACTS_DIR / "workflow-package-v1.json"
VECTOR_PATH = CONTRACTS_DIR / "workflow-package-v1-vectors.json"

PACKAGE_MAX_FILES = 512
PACKAGE_MAX_FILE_BYTES = 1024 * 1024
PACKAGE_MAX_TOTAL_BYTES = 8 * 1024 * 1024
_MARKETPLACE_INDEX_MAX_BYTES = 1024 * 1024
_MARKETPLACE_CATALOG_MAX_ENTRIES = 4096
_DIGEST_DOMAIN = b"hermes.workflow-package.v1\0"

_DIAGNOSTIC_CODES = {
    "package_catalog_entry_limit": "The repository index contains too many package entries.",
    "package_contract_unsupported": "The requested package contract version is unsupported.",
    "package_digest_invalid": "A publisher digest record is structurally invalid.",
    "package_digest_mismatch": "Publisher claims differ from independently computed package bytes.",
    "package_file_count_limit": "The package includes more files than the contract permits.",
    "package_file_size_limit": "A package file exceeds the per-file byte limit.",
    "package_index_invalid": "The repository marketplace index is structurally invalid.",
    "package_index_size_limit": "The repository marketplace index exceeds its byte limit.",
    "package_manifest_invalid": "The workflow package manifest is structurally invalid.",
    "package_member_missing": "A manifest-declared workflow member is missing.",
    "package_path_absolute": "A package member path is absolute.",
    "package_path_backslash": "A package member path contains a backslash.",
    "package_path_collision": "Two package member paths have an ambiguous canonical identity.",
    "package_path_nul": "A package member path contains a NUL byte.",
    "package_path_traversal": "A package member path contains traversal or dot segments.",
    "package_repository_metadata": "A package contains nested repository metadata.",
    "package_root_nested": "Package roots may not overlap or nest.",
    "package_symlink_unsupported": "Distributable symbolic links are unsupported.",
    "package_total_size_limit": "Included package bytes exceed the aggregate byte limit.",
}


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _public_schema(model: type[Any], *, schema_id: str) -> dict[str, Any]:
    schema = model.model_json_schema(by_alias=True, mode="validation")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": schema_id,
        **schema,
    }


def _contract_envelope_from_models() -> dict[str, object]:
    contract = WorkflowPackageContract(
        contract_version=CONTRACT_VERSION,
        package_manifest_schema=_public_schema(
            WorkflowPackageManifest,
            schema_id="https://hermes-agent.dev/contracts/workflow-package-manifest-v1.schema.json",
        ),
        marketplace_index_schema=_public_schema(
            WorkflowPackageIndex,
            schema_id="https://hermes-agent.dev/contracts/workflow-marketplace-index-v1.schema.json",
        ),
        digests_schema=_public_schema(
            WorkflowPackageDigests,
            schema_id="https://hermes-agent.dev/contracts/workflow-package-digests-v1.schema.json",
        ),
        path_rules=PathRules(
            separator="/",
            relative_only=True,
            unicode_normalization="NFC",
            reject_empty_segments=True,
            reject_dot_segments=True,
            reject_backslashes=True,
            reject_nul=True,
            reject_casefold_collisions=True,
            reject_symlinks=True,
            reject_nested_package_roots=True,
            reject_repository_metadata=True,
        ),
        resource_rules=ResourceRules(
            max_files=PACKAGE_MAX_FILES,
            max_file_bytes=PACKAGE_MAX_FILE_BYTES,
            max_total_bytes=PACKAGE_MAX_TOTAL_BYTES,
            max_index_bytes=_MARKETPLACE_INDEX_MAX_BYTES,
            max_catalog_entries=_MARKETPLACE_CATALOG_MAX_ENTRIES,
        ),
        digest_rules=DigestRules(
            algorithm="sha256",
            bytes="exact_no_normalization",
            ordering="unicode_code_point_by_canonical_path",
            included_paths="all_regular_files_below_package_root",
            excluded_paths=["digests.json"],
            domain_separator_base64=base64.b64encode(_DIGEST_DOMAIN).decode("ascii"),
            path_length_encoding="unsigned_64_bit_big_endian",
            file_size_encoding="unsigned_64_bit_big_endian",
            file_hash_encoding="raw_32_byte_sha256",
        ),
        compatibility_rules=CompatibilityRules(
            supported_contract_versions=[CONTRACT_VERSION],
            supported_manifest_schema_versions=[1],
            supported_index_schema_versions=[1],
            semantic_version_standard="SemVer 2.0.0",
            version_comparison="SemVer_2.0.0_precedence_build_metadata_ignored",
            unsupported_versions="fail_closed",
            missing_external_requirements="advisory",
        ),
        diagnostic_codes=_DIAGNOSTIC_CODES,
    )
    return contract.model_dump(mode="json")


def _distribution_digest(files: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256(_DIGEST_DOMAIN)
    for path, content in sorted(files, key=lambda item: item[0]):
        path_bytes = path.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def _content_recipe(content: bytes, *, text: str | None = None) -> dict[str, object]:
    if text is not None:
        return {"encoding": "utf-8", "value": text}
    return {
        "encoding": "base64",
        "value": base64.b64encode(content).decode("ascii"),
    }


def _digest_vector(
    name: str,
    files: list[tuple[str, bytes, dict[str, object]]],
) -> dict[str, object]:
    return {
        "name": name,
        "files": [
            {
                "path": path,
                "content": recipe,
                "expected": {
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                },
            }
            for path, content, recipe in files
        ],
        "expectedSortedPaths": sorted(path for path, _, _ in files),
        "expectedPackageDigest": _distribution_digest([
            (path, content) for path, content, _ in files
        ]),
    }


def _byte_boundary(
    name: str,
    *,
    limit_name: str,
    repeat: str,
    count: int,
    limit: int,
    diagnostic_code: str,
) -> dict[str, object]:
    observed = len(repeat.encode("utf-8")) * count
    accepted = observed <= limit
    return {
        "name": name,
        "limit": limit_name,
        "recipe": {
            "kind": "bytes",
            "encoding": "utf-8",
            "repeat": repeat,
            "count": count,
        },
        "expected": {
            "accepted": accepted,
            "diagnosticCode": None if accepted else diagnostic_code,
            "limit": limit,
            "observed": observed,
            "sha256": hashlib.sha256(repeat.encode("utf-8") * count).hexdigest(),
        },
    }


def _materialize_content_recipe(recipe: dict[str, Any]) -> bytes:
    if recipe["encoding"] == "base64":
        return base64.b64decode(recipe["value"], validate=True)
    if recipe["encoding"] == "utf-8":
        if "repeat" in recipe:
            return (recipe["repeat"] * recipe["count"]).encode("utf-8")
        return recipe["value"].encode("utf-8")
    raise ValueError(f"unsupported content recipe encoding: {recipe['encoding']}")


def _materialize_package_recipe(
    recipe: dict[str, Any],
) -> list[tuple[str, bytes]]:
    package_files = [
        (item["path"], _materialize_content_recipe(item["content"]))
        for item in recipe["files"]
    ]
    for group in recipe["generatedFiles"]:
        content = _materialize_content_recipe(group["content"])
        package_files.extend(
            (
                group["pathTemplate"].format(index=index),
                content,
            )
            for index in range(
                group["startIndex"],
                group["startIndex"] + group["count"],
            )
        )
    return package_files


def _package_boundary(
    name: str,
    *,
    limit_name: str,
    observed: int,
    limit: int,
    diagnostic_code: str,
    files: list[dict[str, Any]] | None = None,
    generated_files: list[dict[str, Any]] | None = None,
) -> dict[str, object]:
    accepted = observed <= limit
    recipe = {
        "kind": "packageFiles",
        "files": files or [],
        "generatedFiles": generated_files or [],
    }
    return {
        "name": name,
        "limit": limit_name,
        "recipe": recipe,
        "expected": {
            "accepted": accepted,
            "diagnosticCode": None if accepted else diagnostic_code,
            "limit": limit,
            "observed": observed,
            "packageDigest": _distribution_digest(_materialize_package_recipe(recipe)),
        },
    }


def _catalog_boundary(
    name: str,
    *,
    count: int,
    limit: int,
    diagnostic_code: str,
) -> dict[str, object]:
    accepted = count <= limit
    return {
        "name": name,
        "limit": "max_catalog_entries",
        "recipe": {
            "kind": "marketplaceIndex",
            "schemaVersion": 1,
            "count": count,
            "entryTemplate": {
                "id": "p{index:04d}",
                "version": "1.0.0",
                "displayName": "P",
                "description": "P",
                "license": "MIT",
                "publisher": "p",
                "tags": ["p"],
                "packagePath": "packages/p{index:04d}",
                "contractVersion": 1,
                "packageDigest": "a" * 64,
            },
        },
        "expected": {
            "accepted": accepted,
            "diagnosticCode": None if accepted else diagnostic_code,
            "limit": limit,
            "observed": count,
        },
    }


def _contract_vectors() -> dict[str, object]:
    manifest = {
        "schemaVersion": 1,
        "id": "laptop-support",
        "version": "1.2.3",
        "displayName": "Laptop Support",
        "description": "Diagnostic and support-bundle workflows for laptops.",
        "license": "MIT",
        "publisher": "example-company",
        "tags": ["diagnostics", "support"],
        "workflows": [
            {
                "definition": "workflows/laptop-diagnostic.yaml",
                "companion": "workflows/laptop-diagnostic.hermes.yaml",
            }
        ],
        "externalRequirements": {
            "runtimes": ["uv"],
            "tools": [],
            "providers": [],
            "services": [],
            "secrets": [],
        },
    }
    index_entry = {
        "id": "laptop-support",
        "version": "1.2.3",
        "displayName": "Laptop Support",
        "description": "Diagnostic and support-bundle workflows for laptops.",
        "license": "MIT",
        "publisher": "example-company",
        "tags": ["diagnostics", "support"],
        "packagePath": "packages/laptop-support",
        "contractVersion": 1,
        "packageDigest": "a" * 64,
    }
    digest_record = {
        "path": "workflow-package.json",
        "size": 2,
        "sha256": hashlib.sha256(b"{} ".rstrip()).hexdigest(),
    }

    utf8 = "Héllø, 世界\n"
    lf = "one\ntwo\n"
    crlf = "one\r\ntwo\r\n"
    unicode_text = "雪\n"
    return {
        "contractVersion": CONTRACT_VERSION,
        "digestVectors": [
            _digest_vector(
                "utf8",
                [
                    (
                        "README.md",
                        utf8.encode("utf-8"),
                        _content_recipe(utf8.encode("utf-8"), text=utf8),
                    )
                ],
            ),
            _digest_vector(
                "binary_base64",
                [
                    (
                        "fixtures/raw.bin",
                        b"\x00\xff\x80\x01",
                        _content_recipe(b"\x00\xff\x80\x01"),
                    )
                ],
            ),
            _digest_vector(
                "line_endings_lf",
                [
                    (
                        "scripts/example.txt",
                        lf.encode("utf-8"),
                        _content_recipe(lf.encode("utf-8"), text=lf),
                    )
                ],
            ),
            _digest_vector(
                "line_endings_crlf",
                [
                    (
                        "scripts/example.txt",
                        crlf.encode("utf-8"),
                        _content_recipe(crlf.encode("utf-8"), text=crlf),
                    )
                ],
            ),
            _digest_vector(
                "empty_bytes",
                [("fixtures/empty", b"", _content_recipe(b"", text=""))],
            ),
            _digest_vector(
                "unicode_paths",
                [
                    (
                        "fixtures/café/猫.txt",
                        unicode_text.encode("utf-8"),
                        _content_recipe(
                            unicode_text.encode("utf-8"), text=unicode_text
                        ),
                    )
                ],
            ),
            _digest_vector(
                "sorting",
                [
                    ("z.txt", b"z", _content_recipe(b"z", text="z")),
                    ("A.txt", b"A", _content_recipe(b"A", text="A")),
                    ("é.txt", b"e", _content_recipe(b"e", text="e")),
                ],
            ),
        ],
        "pathVectors": [
            {
                "name": "unicode_path",
                "input": {"path": "fixtures/café/猫.txt", "kind": "regular"},
                "expected": {
                    "accepted": True,
                    "normalizedPath": "fixtures/café/猫.txt",
                },
            },
            {
                "name": "absolute",
                "input": {"path": "/workflows/unsafe.yaml", "kind": "regular"},
                "expected": {
                    "accepted": False,
                    "diagnosticCode": "package_path_absolute",
                },
            },
            {
                "name": "traversal",
                "input": {"path": "workflows/../secret.yaml", "kind": "regular"},
                "expected": {
                    "accepted": False,
                    "diagnosticCode": "package_path_traversal",
                },
            },
            {
                "name": "backslash",
                "input": {"path": "workflows\\unsafe.yaml", "kind": "regular"},
                "expected": {
                    "accepted": False,
                    "diagnosticCode": "package_path_backslash",
                },
            },
            {
                "name": "nul",
                "input": {"path": "workflows/unsafe\u0000.yaml", "kind": "regular"},
                "expected": {"accepted": False, "diagnosticCode": "package_path_nul"},
            },
            {
                "name": "case_collision",
                "input": {
                    "paths": ["fixtures/Report.json", "fixtures/report.json"],
                    "kind": "regular",
                },
                "expected": {
                    "accepted": False,
                    "diagnosticCode": "package_path_collision",
                },
            },
            {
                "name": "symlink",
                "input": {
                    "path": "scripts/current",
                    "kind": "symlink",
                    "target": "run.py",
                },
                "expected": {
                    "accepted": False,
                    "diagnosticCode": "package_symlink_unsupported",
                },
            },
            {
                "name": "nested_package_root",
                "input": {
                    "paths": [
                        "workflow-package.json",
                        "packages/child/workflow-package.json",
                    ]
                },
                "expected": {
                    "accepted": False,
                    "diagnosticCode": "package_root_nested",
                },
            },
            {
                "name": "repository_metadata",
                "input": {"path": "fixtures/.git/config", "kind": "regular"},
                "expected": {
                    "accepted": False,
                    "diagnosticCode": "package_repository_metadata",
                },
            },
        ],
        "boundaryVectors": [
            _package_boundary(
                "file_count_exact",
                limit_name="max_files",
                observed=PACKAGE_MAX_FILES,
                limit=PACKAGE_MAX_FILES,
                diagnostic_code="package_file_count_limit",
                generated_files=[
                    {
                        "pathTemplate": "fixtures/file-{index:04d}.bin",
                        "startIndex": 0,
                        "count": PACKAGE_MAX_FILES,
                        "content": _content_recipe(b""),
                    }
                ],
            ),
            _package_boundary(
                "file_count_over",
                limit_name="max_files",
                observed=PACKAGE_MAX_FILES + 1,
                limit=PACKAGE_MAX_FILES,
                diagnostic_code="package_file_count_limit",
                generated_files=[
                    {
                        "pathTemplate": "fixtures/file-{index:04d}.bin",
                        "startIndex": 0,
                        "count": PACKAGE_MAX_FILES + 1,
                        "content": _content_recipe(b""),
                    }
                ],
            ),
            _package_boundary(
                "file_bytes_exact",
                limit_name="max_file_bytes",
                observed=PACKAGE_MAX_FILE_BYTES,
                limit=PACKAGE_MAX_FILE_BYTES,
                diagnostic_code="package_file_size_limit",
                files=[
                    {
                        "path": "fixtures/boundary.bin",
                        "content": {
                            "encoding": "utf-8",
                            "repeat": "a",
                            "count": PACKAGE_MAX_FILE_BYTES,
                        },
                    }
                ],
            ),
            _package_boundary(
                "file_bytes_over",
                limit_name="max_file_bytes",
                observed=PACKAGE_MAX_FILE_BYTES + 1,
                limit=PACKAGE_MAX_FILE_BYTES,
                diagnostic_code="package_file_size_limit",
                files=[
                    {
                        "path": "fixtures/boundary.bin",
                        "content": {
                            "encoding": "utf-8",
                            "repeat": "a",
                            "count": PACKAGE_MAX_FILE_BYTES + 1,
                        },
                    }
                ],
            ),
            _package_boundary(
                "total_bytes_exact",
                limit_name="max_total_bytes",
                observed=PACKAGE_MAX_TOTAL_BYTES,
                limit=PACKAGE_MAX_TOTAL_BYTES,
                diagnostic_code="package_total_size_limit",
                generated_files=[
                    {
                        "pathTemplate": "fixtures/chunk-{index:02d}.bin",
                        "startIndex": 0,
                        "count": 8,
                        "content": {
                            "encoding": "utf-8",
                            "repeat": "z",
                            "count": PACKAGE_MAX_FILE_BYTES,
                        },
                    }
                ],
            ),
            _package_boundary(
                "total_bytes_over",
                limit_name="max_total_bytes",
                observed=PACKAGE_MAX_TOTAL_BYTES + 1,
                limit=PACKAGE_MAX_TOTAL_BYTES,
                diagnostic_code="package_total_size_limit",
                files=[
                    {
                        "path": "fixtures/overflow.bin",
                        "content": _content_recipe(b"x", text="x"),
                    }
                ],
                generated_files=[
                    {
                        "pathTemplate": "fixtures/chunk-{index:02d}.bin",
                        "startIndex": 0,
                        "count": 8,
                        "content": {
                            "encoding": "utf-8",
                            "repeat": "z",
                            "count": PACKAGE_MAX_FILE_BYTES,
                        },
                    }
                ],
            ),
            _byte_boundary(
                "index_bytes_exact",
                limit_name="max_index_bytes",
                repeat="i",
                count=_MARKETPLACE_INDEX_MAX_BYTES,
                limit=_MARKETPLACE_INDEX_MAX_BYTES,
                diagnostic_code="package_index_size_limit",
            ),
            _byte_boundary(
                "index_bytes_over",
                limit_name="max_index_bytes",
                repeat="i",
                count=_MARKETPLACE_INDEX_MAX_BYTES + 1,
                limit=_MARKETPLACE_INDEX_MAX_BYTES,
                diagnostic_code="package_index_size_limit",
            ),
            _catalog_boundary(
                "catalog_entries_exact",
                count=_MARKETPLACE_CATALOG_MAX_ENTRIES,
                limit=_MARKETPLACE_CATALOG_MAX_ENTRIES,
                diagnostic_code="package_catalog_entry_limit",
            ),
            _catalog_boundary(
                "catalog_entries_over",
                count=_MARKETPLACE_CATALOG_MAX_ENTRIES + 1,
                limit=_MARKETPLACE_CATALOG_MAX_ENTRIES,
                diagnostic_code="package_catalog_entry_limit",
            ),
        ],
        "validationVectors": [
            {
                "name": "manifest_valid",
                "document": "workflow-package.json",
                "value": manifest,
                "expected": {"accepted": True},
            },
            {
                "name": "manifest_semver_invalid",
                "document": "workflow-package.json",
                "value": {**manifest, "version": "01.2.3"},
                "expected": {
                    "accepted": False,
                    "diagnosticCode": "package_manifest_invalid",
                },
            },
            {
                "name": "manifest_contract_unsupported",
                "document": "workflow-package.json",
                "value": {**manifest, "schemaVersion": 2},
                "expected": {
                    "accepted": False,
                    "diagnosticCode": "package_contract_unsupported",
                },
            },
            {
                "name": "manifest_trust_state_forbidden",
                "document": "workflow-package.json",
                "value": {**manifest, "trusted": True},
                "expected": {
                    "accepted": False,
                    "diagnosticCode": "package_manifest_invalid",
                },
            },
            {
                "name": "index_entry_malformed",
                "document": ".well-known/hermes-workflows/index.json",
                "value": {
                    "schemaVersion": 1,
                    "packages": [{**index_entry, "packageDigest": "bad"}],
                },
                "expected": {
                    "accepted": False,
                    "diagnosticCode": "package_index_invalid",
                },
            },
            {
                "name": "digest_record_malformed",
                "document": "digests.json",
                "value": {
                    "contractVersion": 1,
                    "algorithm": "sha256",
                    "files": [{**digest_record, "sha256": "bad"}],
                    "packageDigest": "b" * 64,
                },
                "expected": {
                    "accepted": False,
                    "diagnosticCode": "package_digest_invalid",
                },
            },
            {
                "name": "publisher_digest_mismatch",
                "document": "digests.json",
                "value": {
                    "contractVersion": 1,
                    "algorithm": "sha256",
                    "files": [digest_record],
                    "packageDigest": "b" * 64,
                },
                "actualPackageDigest": "c" * 64,
                "expected": {
                    "accepted": False,
                    "diagnosticCode": "package_digest_mismatch",
                },
            },
        ],
    }


def render_contract_artifacts() -> tuple[bytes, bytes]:
    """Render both immutable artifacts with stable UTF-8 JSON bytes."""

    return (
        _canonical_json(_contract_envelope_from_models()),
        _canonical_json(_contract_vectors()),
    )


def load_package_contract(path: Path | None = None) -> WorkflowPackageContract:
    """Load the committed artifact that is authoritative at runtime."""

    contract_path = path or CONTRACT_PATH
    payload = json.loads(contract_path.read_bytes())
    return WorkflowPackageContract.model_validate(payload)


__all__ = [
    "CONTRACT_PATH",
    "CONTRACT_VERSION",
    "PACKAGE_MAX_FILE_BYTES",
    "PACKAGE_MAX_FILES",
    "PACKAGE_MAX_TOTAL_BYTES",
    "VECTOR_PATH",
    "load_package_contract",
    "render_contract_artifacts",
]
