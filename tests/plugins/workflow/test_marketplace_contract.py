"""Behavior contracts for the version-one workflow package artifacts."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
import pytest
from pydantic import ValidationError
from scripts import generate_workflow_package_contract as contract_generator

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
    InstalledPackageProvenance,
    PackageReviewAssessment,
    WorkflowMarketplaceSource,
    WorkflowPackageDigestRecord,
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


def valid_index() -> dict[str, Any]:
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


def valid_provenance() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "identity": {"sourceKey": "company", "packageId": "laptop-support"},
        "sourceName": "company",
        "repositoryUrl": "https://example.com/company/workflows.git",
        "configuredRef": "main",
        "resolvedCommit": "a" * 40,
        "packagePath": "packages/laptop-support",
        "packageVersion": "1.2.3",
        "contractVersion": 1,
        "distributionDigest": SHA256_A,
        "installedAt": "2026-09-03T12:00:00Z",
        "actor": "test",
        "workflowPaths": ["workflows/laptop-diagnostic.yaml"],
    }


def _materialize(recipe: dict[str, Any]) -> bytes:
    encoding = recipe["encoding"]
    if encoding == "base64":
        return base64.b64decode(str(recipe["value"]), validate=True)
    if encoding == "utf-8":
        value = str(recipe.get("value", ""))
        if "repeat" in recipe:
            value = str(recipe["repeat"]) * int(recipe["count"])
        return value.encode("utf-8")
    raise AssertionError(f"unsupported test recipe encoding: {encoding}")


def _materialize_package(recipe: dict[str, Any]) -> list[tuple[str, bytes]]:
    assert recipe["kind"] == "packageFiles"
    files: list[tuple[str, bytes]] = []
    for item in recipe["files"]:
        files.append((item["path"], _materialize(item["content"])))
    for group in recipe["generatedFiles"]:
        for index in range(group["startIndex"], group["startIndex"] + group["count"]):
            files.append((
                group["pathTemplate"].format(index=index),
                _materialize(group["content"]),
            ))
    return files


def _materialize_catalog(recipe: dict[str, Any]) -> dict[str, Any]:
    assert recipe["kind"] == "marketplaceIndex"
    entries = []
    for index in range(recipe["count"]):
        entries.append({
            key: value.format(index=index) if isinstance(value, str) else value
            for key, value in recipe["entryTemplate"].items()
        })
    return {"schemaVersion": recipe["schemaVersion"], "packages": entries}


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


def test_public_manifest_rejects_internal_snake_case_field_names() -> None:
    manifest = valid_manifest()
    manifest["schema_version"] = manifest.pop("schemaVersion")
    manifest["display_name"] = manifest.pop("displayName")
    manifest["external_requirements"] = manifest.pop("externalRequirements")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WorkflowPackageManifest.model_validate(manifest)


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


def test_package_paths_reject_non_nfc_text() -> None:
    manifest = valid_manifest()
    manifest["workflows"] = [{"definition": "workflows/cafe\u0301.yaml"}]

    with pytest.raises(ValidationError, match="NFC"):
        WorkflowPackageManifest.model_validate(manifest)


def test_package_manifest_rejects_casefold_member_collisions() -> None:
    manifest = valid_manifest()
    manifest["workflows"] = [
        {"definition": "workflows/Report.yaml"},
        {"definition": "workflows/report.yaml"},
    ]

    with pytest.raises(ValidationError, match="alias"):
        WorkflowPackageManifest.model_validate(manifest)


def test_package_digests_reject_casefold_path_collisions() -> None:
    digests = valid_digests()
    digests["files"] = [
        {"path": "workflows/Report.yaml", "size": 1, "sha256": SHA256_A},
        {"path": "workflows/report.yaml", "size": 1, "sha256": SHA256_B},
    ]

    with pytest.raises(ValidationError, match="unique"):
        WorkflowPackageDigests.model_validate(digests)


def test_marketplace_index_rejects_casefold_and_nested_package_roots() -> None:
    first = valid_index()["packages"][0]
    case_collision = {
        "schemaVersion": 1,
        "packages": [
            {**first, "id": "first", "packagePath": "packages/Example"},
            {**first, "id": "second", "packagePath": "packages/example"},
        ],
    }
    nested = {
        "schemaVersion": 1,
        "packages": [
            {**first, "id": "first", "packagePath": "packages/example"},
            {**first, "id": "second", "packagePath": "packages/example/child"},
        ],
    }

    with pytest.raises(ValidationError, match="unique"):
        WorkflowPackageIndex.model_validate(case_collision)
    with pytest.raises(ValidationError, match="nested"):
        WorkflowPackageIndex.model_validate(nested)

    siblings = {
        "schemaVersion": 1,
        "packages": [
            {**first, "id": "first", "packagePath": "packages/example"},
            {**first, "id": "second", "packagePath": "packages/example-child"},
        ],
    }
    assert len(WorkflowPackageIndex.model_validate(siblings).packages) == 2


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


@pytest.mark.parametrize(
    "repository_url",
    [
        "https://token@example.com/company/workflows.git",
        "https://example.com/company/workflows.git?access_token=secret",
        "https://example.com/company/workflows.git?apiKey=secret",
    ],
)
@pytest.mark.parametrize("model_name", ["source", "direct_install", "provenance"])
def test_persistable_repository_identities_reject_embedded_credentials(
    repository_url: str,
    model_name: str,
) -> None:
    if model_name == "source":
        model = WorkflowMarketplaceSource
        value = {
            "name": "company",
            "repositoryUrl": repository_url,
            "ref": "main",
            "enabled": True,
        }
    elif model_name == "direct_install":
        model = InstallRequest
        value = {"identifier": repository_url, "ref": "main"}
    else:
        model = InstalledPackageProvenance
        value = {**valid_provenance(), "repositoryUrl": repository_url}

    with pytest.raises(ValidationError, match="credentials"):
        model.model_validate(value)


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
        elif recipe["kind"] == "packageFiles":
            files = _materialize_package(recipe)
            paths = [path for path, _ in files]
            assert len(paths) == len(set(paths))
            for path in paths:
                WorkflowPackageDigestRecord.model_validate({
                    "path": path,
                    "size": 0,
                    "sha256": SHA256_A,
                })

            observed = {
                "max_files": len(files),
                "max_file_bytes": max(map(lambda item: len(item[1]), files)),
                "max_total_bytes": sum(len(content) for _, content in files),
            }
            limits = load_package_contract().resource_rules
            violations = {
                name
                for name, value in observed.items()
                if value > getattr(limits, name)
            }
            expected_violations = (
                set() if vector["expected"]["accepted"] else {vector["limit"]}
            )
            assert violations == expected_violations
            assert observed[vector["limit"]] == vector["expected"]["observed"]
        elif recipe["kind"] == "marketplaceIndex":
            catalog = _materialize_catalog(recipe)
            encoded_catalog = json.dumps(
                catalog,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            assert (
                len(encoded_catalog)
                <= load_package_contract().resource_rules.max_index_bytes
            )
            if vector["expected"]["accepted"]:
                parsed = WorkflowPackageIndex.model_validate(catalog)
                assert len(parsed.packages) == vector["expected"]["observed"]
            else:
                with pytest.raises(ValidationError):
                    WorkflowPackageIndex.model_validate(catalog)
            assert len(catalog["packages"]) == vector["expected"]["observed"]
        else:
            raise AssertionError(f"unsupported boundary recipe: {recipe['kind']}")


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


def test_generator_write_preserves_artifact_when_atomic_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "workflow-package-v1.json"
    artifact.write_bytes(b"published contract\n")
    original_entries = set(tmp_path.iterdir())

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("simulated interrupted replacement")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="interrupted replacement"):
        contract_generator._write(artifact, b"new contract\n")

    assert artifact.read_bytes() == b"published contract\n"
    assert set(tmp_path.iterdir()) == original_entries
