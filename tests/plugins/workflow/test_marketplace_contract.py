"""Behavior contracts for the version-one workflow package artifacts."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
import pytest
from pydantic import ValidationError

from plugins.workflow.marketplace.contract import (
    CONTRACT_PATH,
    PACKAGE_MAX_FILE_BYTES,
    PACKAGE_MAX_FILES,
    PACKAGE_MAX_TOTAL_BYTES,
    VECTOR_PATH,
    load_package_contract,
    render_contract_artifacts,
)
from plugins.workflow.marketplace.models import (
    InstallRequest,
    PackageReviewAssessment,
    WorkflowPackageDigests,
    WorkflowPackageIndex,
    WorkflowPackageManifest,
)


ROOT = Path(__file__).resolve().parents[3]
GENERATOR = ROOT / "scripts" / "generate_workflow_package_contract.py"
SHA256_A = "a" * 64
SHA256_B = "b" * 64


def valid_manifest() -> dict[str, object]:
    return {
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
            },
            {"definition": "workflows/collect-support-bundle.yaml"},
        ],
        "externalRequirements": {
            "runtimes": ["uv"],
            "tools": [],
            "providers": [],
            "services": [],
            "secrets": [],
        },
    }


def valid_index() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "packages": [
            {
                "id": "laptop-support",
                "version": "1.2.3",
                "displayName": "Laptop Support",
                "description": "Diagnostic and support-bundle workflows for laptops.",
                "license": "MIT",
                "publisher": "example-company",
                "tags": ["diagnostics", "support"],
                "packagePath": "packages/laptop-support",
                "contractVersion": 1,
                "packageDigest": SHA256_A,
            }
        ],
    }


def valid_digests() -> dict[str, object]:
    return {
        "contractVersion": 1,
        "algorithm": "sha256",
        "files": [
            {"path": "workflow-package.json", "size": 10, "sha256": SHA256_A},
            {
                "path": "workflows/laptop-diagnostic.yaml",
                "size": 20,
                "sha256": SHA256_B,
            },
        ],
        "packageDigest": "c" * 64,
    }


def _materialize(recipe: dict[str, object]) -> bytes:
    encoding = recipe["encoding"]
    if encoding == "base64":
        return base64.b64decode(str(recipe["value"]), validate=True)
    if encoding == "utf-8":
        value = str(recipe.get("value", ""))
        if "repeat" in recipe:
            value = str(recipe["repeat"]) * int(recipe["count"])
        return value.encode("utf-8")
    raise AssertionError(f"unsupported test recipe encoding: {encoding}")


def _composite_digest(files: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256(b"hermes.workflow-package.v1\0")
    for path, content in sorted(files):
        path_bytes = path.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def test_contract_artifacts_are_deterministic_and_self_validating() -> None:
    contract_bytes, vector_bytes = render_contract_artifacts()

    assert contract_bytes == CONTRACT_PATH.read_bytes()
    assert vector_bytes == VECTOR_PATH.read_bytes()
    assert contract_bytes.endswith(b"\n") and not contract_bytes.endswith(b"\n\n")
    assert vector_bytes.endswith(b"\n") and not vector_bytes.endswith(b"\n\n")

    contract = load_package_contract()
    assert contract.contract_version == 1
    assert contract.resource_rules.max_files == 512
    assert contract.resource_rules.max_file_bytes == 1_048_576
    assert contract.resource_rules.max_total_bytes == 8_388_608
    assert (
        PACKAGE_MAX_FILES,
        PACKAGE_MAX_FILE_BYTES,
        PACKAGE_MAX_TOTAL_BYTES,
    ) == (
        contract.resource_rules.max_files,
        contract.resource_rules.max_file_bytes,
        contract.resource_rules.max_total_bytes,
    )
    assert (
        contract.digest_rules.included_paths == "all_regular_files_below_package_root"
    )
    assert contract.compatibility_rules.version_comparison == (
        "SemVer_2.0.0_precedence_build_metadata_ignored"
    )
    for schema in (
        contract.package_manifest_schema,
        contract.marketplace_index_schema,
        contract.digests_schema,
    ):
        Draft202012Validator.check_schema(schema)

    Draft202012Validator(contract.package_manifest_schema).validate(valid_manifest())
    Draft202012Validator(contract.marketplace_index_schema).validate(valid_index())
    Draft202012Validator(contract.digests_schema).validate(valid_digests())


def test_generated_manifest_schema_enforces_public_token_syntax() -> None:
    schema = load_package_contract().package_manifest_schema
    invalid = valid_manifest()
    invalid["tags"] = ["Not Canonical"]

    with pytest.raises(JsonSchemaValidationError):
        Draft202012Validator(schema).validate(invalid)


@pytest.mark.parametrize("field", ["trusted", "editorState", "layout"])
def test_package_manifest_rejects_trust_and_editor_state(field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WorkflowPackageManifest.model_validate({**valid_manifest(), field: True})


@pytest.mark.parametrize(
    "version",
    ["1", "1.2", "01.2.3", "1.02.3", "1.2.03", "v1.2.3", "1.2.3-01"],
)
def test_package_manifest_rejects_invalid_semantic_versions(version: str) -> None:
    manifest = {**valid_manifest(), "version": version}

    with pytest.raises(ValidationError, match="semantic version"):
        WorkflowPackageManifest.model_validate(manifest)


@pytest.mark.parametrize(
    "missing",
    ["displayName", "description", "license", "publisher", "tags"],
)
def test_package_manifest_requires_publishing_metadata(missing: str) -> None:
    manifest = valid_manifest()
    manifest.pop(missing)

    with pytest.raises(ValidationError):
        WorkflowPackageManifest.model_validate(manifest)


def test_package_manifest_rejects_unsupported_schema_version() -> None:
    with pytest.raises(ValidationError):
        WorkflowPackageManifest.model_validate({**valid_manifest(), "schemaVersion": 2})


@pytest.mark.parametrize(
    "workflows",
    [
        [
            {"definition": "workflows/a.yaml"},
            {"definition": "workflows/a.yaml"},
        ],
        [
            {"definition": "workflows/a.yaml", "companion": "workflows/a.meta.yaml"},
            {"definition": "workflows/b.yaml", "companion": "workflows/a.meta.yaml"},
        ],
        [
            {"definition": "workflows/a.yaml", "companion": "workflows/b.yaml"},
            {"definition": "workflows/b.yaml"},
        ],
    ],
)
def test_package_manifest_rejects_duplicate_definitions_and_companion_aliases(
    workflows: list[dict[str, str]],
) -> None:
    with pytest.raises(ValidationError, match="workflow member path"):
        WorkflowPackageManifest.model_validate({
            **valid_manifest(),
            "workflows": workflows,
        })


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["packages"][0].__setitem__("packageDigest", "not-a-digest"),
        lambda value: value["packages"][0].__setitem__("credential", "secret"),
        lambda value: value["packages"].append(dict(value["packages"][0])),
        lambda value: value["packages"][0].pop("packagePath"),
    ],
)
def test_marketplace_index_rejects_malformed_entries(mutate) -> None:
    value = valid_index()
    mutate(value)

    with pytest.raises(ValidationError):
        WorkflowPackageIndex.model_validate(value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["files"][0].__setitem__("sha256", "A" * 64),
        lambda value: value["files"][0].__setitem__("size", -1),
        lambda value: value["files"].append(dict(value["files"][0])),
        lambda value: value["files"].reverse(),
        lambda value: value.__setitem__("files", []),
        lambda value: value["files"][0].__setitem__("editorState", {}),
    ],
)
def test_digest_records_reject_malformed_or_ambiguous_content(mutate) -> None:
    value = valid_digests()
    mutate(value)

    with pytest.raises(ValidationError):
        WorkflowPackageDigests.model_validate(value)


def test_marketplace_public_review_and_request_models_are_strict() -> None:
    assessment = PackageReviewAssessment.model_validate({
        "packageDigest": SHA256_A,
        "reviewDigest": SHA256_B,
        "workflowNames": ["laptop-diagnostic"],
        "blockers": [],
        "advisories": [],
        "externalRequirements": valid_manifest()["externalRequirements"],
    })
    assert assessment.package_digest == SHA256_A

    with pytest.raises(ValidationError):
        InstallRequest.model_validate({
            "identifier": "company/laptop-support",
            "accessToken": "must-not-cross-the-boundary",
        })


def test_shared_vectors_cover_exact_bytes_paths_and_all_boundaries() -> None:
    vectors = json.loads(VECTOR_PATH.read_bytes())

    digest_vectors = {item["name"]: item for item in vectors["digestVectors"]}
    assert {
        "utf8",
        "binary_base64",
        "line_endings_lf",
        "line_endings_crlf",
        "empty_bytes",
        "unicode_paths",
        "sorting",
    } <= digest_vectors.keys()

    for vector in digest_vectors.values():
        files: list[tuple[str, bytes]] = []
        for item in vector["files"]:
            content = _materialize(item["content"])
            assert len(content) == item["expected"]["size"]
            assert hashlib.sha256(content).hexdigest() == item["expected"]["sha256"]
            files.append((item["path"], content))
        assert [path for path, _ in sorted(files)] == vector["expectedSortedPaths"]
        assert _composite_digest(files) == vector["expectedPackageDigest"]

    assert (
        digest_vectors["line_endings_lf"]["expectedPackageDigest"]
        != digest_vectors["line_endings_crlf"]["expectedPackageDigest"]
    )

    path_vectors = {item["name"]: item for item in vectors["pathVectors"]}
    assert {
        "unicode_path",
        "absolute",
        "traversal",
        "backslash",
        "nul",
        "case_collision",
        "symlink",
    } <= path_vectors.keys()
    assert (
        path_vectors["unicode_path"]["expected"]["normalizedPath"]
        == "fixtures/café/猫.txt"
    )
    assert (
        path_vectors["absolute"]["expected"]["diagnosticCode"]
        == "package_path_absolute"
    )
    assert (
        path_vectors["traversal"]["expected"]["diagnosticCode"]
        == "package_path_traversal"
    )
    assert (
        path_vectors["backslash"]["expected"]["diagnosticCode"]
        == "package_path_backslash"
    )
    assert path_vectors["nul"]["expected"]["diagnosticCode"] == "package_path_nul"
    assert (
        path_vectors["case_collision"]["expected"]["diagnosticCode"]
        == "package_path_collision"
    )
    assert (
        path_vectors["symlink"]["expected"]["diagnosticCode"]
        == "package_symlink_unsupported"
    )

    boundary_vectors = {item["name"]: item for item in vectors["boundaryVectors"]}
    assert {
        "file_count_exact",
        "file_count_over",
        "file_bytes_exact",
        "file_bytes_over",
        "total_bytes_exact",
        "total_bytes_over",
        "index_bytes_exact",
        "index_bytes_over",
        "catalog_entries_exact",
        "catalog_entries_over",
    } == boundary_vectors.keys()
    for vector in boundary_vectors.values():
        recipe = vector["recipe"]
        if recipe["kind"] == "bytes":
            content = _materialize(recipe)
            assert len(content) == vector["expected"]["observed"]
            assert hashlib.sha256(content).hexdigest() == vector["expected"]["sha256"]
        else:
            assert recipe["count"] == vector["expected"]["observed"]


def test_generator_check_mode_and_argument_contract() -> None:
    checked = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr

    for args in ([], ["--check", "--write"], ["--unknown"]):
        rejected = subprocess.run(
            [sys.executable, str(GENERATOR), *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert rejected.returncode != 0
