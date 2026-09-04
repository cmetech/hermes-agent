"""Filesystem and integrity contracts for workflow package distributions."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import unicodedata
from typing import Any

import pytest

import plugins.workflow.marketplace.package as package_module
from plugins.workflow.marketplace.contract import load_package_contract
from plugins.workflow.marketplace.package import (
    WorkflowMarketplaceError,
    compute_distribution_digest,
    load_distribution,
    load_repository_index,
    scan_package_files,
)
from plugins.workflow.trust import WorkflowResourceReadBudget


FIXTURE_REPOSITORY = Path(__file__).parent / "fixtures" / "marketplace" / "repository"
DOMAIN = b"hermes.workflow-package.v1\0"
# Independently calculated from the checked-in laptop-support fixture bytes.
LAPTOP_SUPPORT_DIGEST = (
    "c24e87da0d7407fc597390021b75043d27dff5a6cd912318fe3796bf3d2678c5"
)


def _json_bytes(value: object, *, compact: bool = False) -> bytes:
    separators = (",", ":") if compact else None
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=None if compact else 2,
            separators=separators,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_json(path: Path, value: object, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value, compact=compact))


def _independent_digest(files: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256(DOMAIN)
    for relative_path, content in sorted(files):
        path_bytes = relative_path.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def _included_bytes(root: Path) -> list[tuple[str, bytes]]:
    return [
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink() and path.name != "digests.json"
    ]


def _publish_package(root: Path) -> str:
    included = _included_bytes(root)
    digest = _independent_digest(included)
    _write_json(
        root / "digests.json",
        {
            "algorithm": "sha256",
            "contractVersion": 1,
            "files": [
                {
                    "path": relative_path,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                }
                for relative_path, content in sorted(included)
            ],
            "packageDigest": digest,
        },
    )
    return digest


def _package_index_entry(package_root: Path, repository_root: Path) -> dict[str, Any]:
    manifest = json.loads((package_root / "workflow-package.json").read_bytes())
    digests = json.loads((package_root / "digests.json").read_bytes())
    return {
        "contractVersion": 1,
        "description": manifest["description"],
        "displayName": manifest["displayName"],
        "id": manifest["id"],
        "license": manifest["license"],
        "packageDigest": digests["packageDigest"],
        "packagePath": package_root.relative_to(repository_root).as_posix(),
        "publisher": manifest["publisher"],
        "tags": manifest["tags"],
        "version": manifest["version"],
    }


def _write_index(repository_root: Path, entries: list[dict[str, Any]]) -> None:
    _write_json(
        repository_root / ".well-known" / "hermes-workflows" / "index.json",
        {"schemaVersion": 1, "packages": sorted(entries, key=lambda item: item["id"])},
    )


def _manifest(package_id: str = "package") -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "id": package_id,
        "version": "1.0.0",
        "displayName": "P",
        "description": "P",
        "license": "M",
        "publisher": "P",
        "tags": ["p"],
        "workflows": [{"definition": "workflow.yaml"}],
        "externalRequirements": {
            "runtimes": [],
            "tools": [],
            "providers": [],
            "services": [],
            "secrets": [],
        },
    }


def _new_package(root: Path, package_id: str = "package") -> Path:
    root.mkdir(parents=True)
    _write_json(root / "workflow-package.json", _manifest(package_id))
    (root / "workflow.yaml").write_bytes(b"name: package\n")
    _publish_package(root)
    return root


@pytest.fixture
def repository_root(tmp_path: Path) -> Path:
    target = tmp_path / "repository"
    shutil.copytree(FIXTURE_REPOSITORY, target)
    return target


@pytest.fixture
def package_root(repository_root: Path) -> Path:
    return repository_root / "packages" / "laptop-support"


def _assert_code(
    error: pytest.ExceptionInfo[WorkflowMarketplaceError], code: str
) -> None:
    assert error.value.code == code
    assert code in str(error.value)


def test_load_distribution_verifies_every_package_owned_byte(
    package_root: Path,
) -> None:
    distribution = load_distribution(package_root)

    assert distribution.manifest.id == "laptop-support"
    assert distribution.digest == distribution.publisher_digests.package_digest
    assert distribution.digest == LAPTOP_SUPPORT_DIGEST
    assert [item.relative_path for item in distribution.files] == sorted(
        item.relative_path for item in distribution.files
    )
    assert distribution.covered_paths == tuple(
        item.relative_path for item in distribution.files if item.included
    )
    assert "digests.json" not in distribution.covered_paths
    assert "scripts/collect.py" in distribution.covered_paths


def test_scan_and_digest_preserve_binary_bytes_and_line_endings(tmp_path: Path) -> None:
    root = tmp_path / "bytes"
    root.mkdir()
    (root / "binary.bin").write_bytes(b"\x00\xff\x80\x01")
    (root / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
    (root / "lf.txt").write_bytes(b"one\ntwo\n")
    (root / "digests.json").write_bytes(b"excluded claim bytes\r\n")

    files = scan_package_files(root)
    by_path = {item.relative_path: item for item in files}

    assert by_path["binary.bin"].content == b"\x00\xff\x80\x01"
    assert by_path["crlf.txt"].content == b"one\r\ntwo\r\n"
    assert by_path["lf.txt"].content == b"one\ntwo\n"
    assert by_path["crlf.txt"].sha256 != by_path["lf.txt"].sha256
    assert not by_path["digests.json"].included
    assert compute_distribution_digest(files) == _independent_digest([
        ("binary.bin", b"\x00\xff\x80\x01"),
        ("crlf.txt", b"one\r\ntwo\r\n"),
        ("lf.txt", b"one\ntwo\n"),
    ])


def test_changing_only_digest_file_format_does_not_change_composite(
    package_root: Path,
) -> None:
    original = load_distribution(package_root)
    claims = json.loads((package_root / "digests.json").read_bytes())
    (package_root / "digests.json").write_bytes(_json_bytes(claims, compact=True))

    reformatted = load_distribution(package_root)

    assert reformatted.digest == original.digest


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("symlink", "package_symlink_unsupported"),
        ("case_collision", "package_path_collision"),
        ("non_nfc", "package_path_collision"),
        ("changed_script", "package_digest_mismatch"),
        ("nested_manifest", "package_root_nested"),
        ("repository_metadata", "package_repository_metadata"),
    ],
)
def test_load_distribution_fails_closed(
    package_root: Path,
    mutation: str,
    code: str,
) -> None:
    if mutation == "symlink":
        (package_root / "scripts" / "current").symlink_to("collect.py")
    elif mutation == "case_collision":
        (package_root / "fixtures" / "Report.json").write_bytes(b"{}")
        (package_root / "fixtures" / "report.json").write_bytes(b"{}")
        observed = {
            path.name
            for path in (package_root / "fixtures").iterdir()
            if path.name.casefold() == "report.json"
        }
        if len(observed) != 2:
            pytest.skip("host filesystem is case-insensitive")
    elif mutation == "non_nfc":
        decomposed = unicodedata.normalize("NFD", "café.txt")
        assert decomposed != "café.txt"
        (package_root / "fixtures" / decomposed).write_bytes(b"ambiguous")
    elif mutation == "changed_script":
        (package_root / "scripts" / "collect.py").write_bytes(b"print('changed')\n")
    elif mutation == "nested_manifest":
        nested = package_root / "fixtures" / "child"
        nested.mkdir()
        (nested / "workflow-package.json").write_bytes(b"{}")
    else:
        metadata = package_root / "fixtures" / ".git"
        metadata.mkdir()
        (metadata / "config").write_bytes(b"[core]\n")

    with pytest.raises(WorkflowMarketplaceError, match=code) as error:
        load_distribution(package_root)

    _assert_code(error, code)


def test_package_root_itself_must_not_be_a_symlink(
    package_root: Path, tmp_path: Path
) -> None:
    link = tmp_path / "linked-package"
    link.symlink_to(package_root, target_is_directory=True)

    with pytest.raises(WorkflowMarketplaceError, match="package_symlink_unsupported"):
        load_distribution(link)


@pytest.mark.parametrize(
    ("member_path", "code"),
    [
        ("/absolute.yaml", "package_path_absolute"),
        ("C:/absolute.yaml", "package_path_absolute"),
        ("C:workflow.yaml", "package_path_absolute"),
        ("safe/C:workflow.yaml", "package_path_absolute"),
        ("../outside.yaml", "package_path_traversal"),
        ("workflows/../outside.yaml", "package_path_traversal"),
        (r"workflows\unsafe.yaml", "package_path_backslash"),
        ("workflows/unsafe\x00.yaml", "package_path_nul"),
    ],
)
def test_manifest_rejects_noncanonical_member_paths(
    package_root: Path,
    member_path: str,
    code: str,
) -> None:
    manifest_path = package_root / "workflow-package.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["workflows"][0]["definition"] = member_path
    _write_json(manifest_path, manifest)

    with pytest.raises(WorkflowMarketplaceError, match=code) as error:
        load_distribution(package_root)

    _assert_code(error, code)


@pytest.mark.parametrize("relative_path", ["C:workflow.yaml", "safe/C:workflow.yaml"])
def test_scan_rejects_drive_relative_path_components(
    tmp_path: Path,
    relative_path: str,
) -> None:
    root = tmp_path / "drive-relative"
    root.mkdir()
    candidate = root.joinpath(*relative_path.split("/"))
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(b"unsafe")
    except OSError:
        pytest.skip("host filesystem does not support colon-bearing filenames")

    with pytest.raises(WorkflowMarketplaceError) as error:
        scan_package_files(root)

    _assert_code(error, "package_path_absolute")


def test_manifest_rejects_duplicate_definition_membership(package_root: Path) -> None:
    manifest_path = package_root / "workflow-package.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["workflows"].append(dict(manifest["workflows"][0]))
    _write_json(manifest_path, manifest)

    with pytest.raises(WorkflowMarketplaceError, match="package_manifest_invalid"):
        load_distribution(package_root)


@pytest.mark.parametrize("field", ["definition", "companion"])
def test_manifest_requires_every_declared_member(
    package_root: Path,
    field: str,
) -> None:
    manifest_path = package_root / "workflow-package.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["workflows"][0][field] = "workflows/missing.hermes.yaml"
    _write_json(manifest_path, manifest)

    with pytest.raises(WorkflowMarketplaceError, match="package_member_missing"):
        load_distribution(package_root)


@pytest.mark.parametrize("claim", ["missing", "unexpected"])
def test_digest_claim_paths_must_exactly_cover_included_files(
    package_root: Path,
    claim: str,
) -> None:
    path = package_root / "digests.json"
    digests = json.loads(path.read_bytes())
    if claim == "missing":
        digests["files"].pop()
    else:
        digests["files"].append({
            "path": "unexpected.bin",
            "sha256": hashlib.sha256(b"").hexdigest(),
            "size": 0,
        })
        digests["files"].sort(key=lambda item: item["path"])
    _write_json(path, digests)

    with pytest.raises(WorkflowMarketplaceError, match="package_digest_mismatch"):
        load_distribution(package_root)


def test_expected_digest_is_an_independent_required_claim(package_root: Path) -> None:
    with pytest.raises(WorkflowMarketplaceError, match="package_digest_mismatch"):
        load_distribution(package_root, expected_digest="0" * 64)


@pytest.mark.parametrize(
    ("offset", "code"),
    [
        (0, None),
        (1, "package_file_size_limit"),
    ],
)
def test_per_file_byte_limit_is_inclusive(
    tmp_path: Path,
    offset: int,
    code: str | None,
) -> None:
    limits = load_package_contract().resource_rules
    root = _new_package(tmp_path / f"file-size-{offset}")
    (root / "boundary.bin").write_bytes(b"x" * (limits.max_file_bytes + offset))
    _publish_package(root)

    if code is None:
        assert load_distribution(root).manifest.id == "package"
    else:
        with pytest.raises(WorkflowMarketplaceError, match=code):
            load_distribution(root)


@pytest.mark.parametrize(
    ("offset", "code"),
    [
        (0, None),
        (1, "package_file_count_limit"),
    ],
)
def test_included_file_count_limit_is_inclusive(
    tmp_path: Path,
    offset: int,
    code: str | None,
) -> None:
    limits = load_package_contract().resource_rules
    root = _new_package(tmp_path / f"file-count-{offset}")
    existing = len(_included_bytes(root))
    resources = root / "fixtures"
    resources.mkdir()
    for index in range(limits.max_files + offset - existing):
        (resources / f"empty-{index:04d}.bin").write_bytes(b"")
    _publish_package(root)

    if code is None:
        distribution = load_distribution(root)
        assert len(distribution.covered_paths) == limits.max_files
    else:
        with pytest.raises(WorkflowMarketplaceError, match=code):
            load_distribution(root)


def test_file_count_limit_rejects_before_reading_the_513th_included_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits = load_package_contract().resource_rules
    root = _new_package(tmp_path / "pre-read-file-count")
    existing = len(_included_bytes(root))
    resources = root / "fixtures"
    resources.mkdir()
    for index in range(limits.max_files + 1 - existing):
        (resources / f"empty-{index:04d}.bin").write_bytes(b"")
    _publish_package(root)
    opened_included: list[str] = []
    real_read_file_at = package_module._read_file_at

    def observe_read(*args, **kwargs):
        relative_path = args[3]
        if relative_path != "digests.json":
            opened_included.append(relative_path)
        return real_read_file_at(*args, **kwargs)

    monkeypatch.setattr(package_module, "_read_file_at", observe_read)

    with pytest.raises(WorkflowMarketplaceError) as error:
        load_distribution(root)

    _assert_code(error, "package_file_count_limit")
    assert len(opened_included) == limits.max_files


def test_traversal_entry_limit_accepts_exact_boundary(tmp_path: Path) -> None:
    limit = load_package_contract().resource_rules.max_traversal_entries
    root = tmp_path / "traversal-exact"
    root.mkdir()
    for index in range(limit):
        (root / f"empty-{index:04d}").mkdir()

    assert scan_package_files(root) == ()


def test_traversal_entry_limit_streams_and_stops_before_entry_4097_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limit = load_package_contract().resource_rules.max_traversal_entries
    root = tmp_path / "traversal-over"
    root.mkdir()
    for index in range(limit + 128):
        (root / f"empty-{index:04d}").mkdir()
    real_scandir = package_module.os.scandir
    yielded = 0

    class CountingScandir:
        def __init__(self, target) -> None:
            self._inner = real_scandir(target)

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal yielded
            entry = next(self._inner)
            yielded += 1
            if yielded > limit + 1:
                raise AssertionError(
                    "scanner consumed beyond the bounded rejection entry"
                )
            return entry

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            self.close()

        def close(self) -> None:
            self._inner.close()

    monkeypatch.setattr(package_module.os, "scandir", CountingScandir)

    with pytest.raises(WorkflowMarketplaceError) as error:
        scan_package_files(root)

    _assert_code(error, "package_traversal_limit")
    assert yielded == limit + 1


def _fill_package_to_total(root: Path, target: int, per_file_limit: int) -> None:
    remaining = target - sum(len(content) for _, content in _included_bytes(root))
    assert remaining > 0
    resources = root / "fixtures"
    resources.mkdir(exist_ok=True)
    index = 0
    while remaining:
        size = min(remaining, per_file_limit)
        (resources / f"chunk-{index:02d}.bin").write_bytes(b"z" * size)
        remaining -= size
        index += 1


@pytest.mark.parametrize(
    ("offset", "code"),
    [
        (0, None),
        (1, "package_total_size_limit"),
    ],
)
def test_total_included_byte_limit_is_inclusive(
    tmp_path: Path,
    offset: int,
    code: str | None,
) -> None:
    limits = load_package_contract().resource_rules
    root = _new_package(tmp_path / f"total-size-{offset}")
    _fill_package_to_total(
        root,
        limits.max_total_bytes + offset,
        limits.max_file_bytes,
    )
    _publish_package(root)

    if code is None:
        distribution = load_distribution(root)
        assert sum(item.size for item in distribution.files if item.included) == (
            limits.max_total_bytes
        )
    else:
        with pytest.raises(WorkflowMarketplaceError, match=code):
            load_distribution(root)


def test_supplied_resource_budget_accounts_for_exact_included_bytes(
    package_root: Path,
) -> None:
    limits = load_package_contract().resource_rules
    budget = WorkflowResourceReadBudget(
        max_file_bytes=limits.max_file_bytes,
        max_total_bytes=limits.max_total_bytes,
        max_files=limits.max_files,
    )

    distribution = load_distribution(package_root, read_budget=budget)

    assert budget.files_read == len(distribution.covered_paths)
    assert budget.bytes_read == sum(
        item.size for item in distribution.files if item.included
    )


def test_supplied_resource_budget_can_be_more_restrictive(package_root: Path) -> None:
    budget = WorkflowResourceReadBudget(
        max_file_bytes=1024 * 1024,
        max_total_bytes=8 * 1024 * 1024,
        max_files=1,
    )

    with pytest.raises(WorkflowMarketplaceError, match="package_file_count_limit"):
        load_distribution(package_root, read_budget=budget)


def test_sealed_authenticated_budget_must_cover_the_same_exact_bytes(
    package_root: Path,
) -> None:
    contents = dict(_included_bytes(package_root))
    budget = WorkflowResourceReadBudget.from_authenticated(package_root, contents)

    distribution = load_distribution(package_root, read_budget=budget)

    assert distribution.covered_paths == tuple(sorted(contents))
    (package_root / "scripts" / "collect.py").write_bytes(b"changed\n")
    with pytest.raises(WorkflowMarketplaceError, match="package_digest_mismatch"):
        load_distribution(package_root, read_budget=budget)


def test_repository_index_verifies_two_nonoverlapping_sibling_packages(
    repository_root: Path,
) -> None:
    index = load_repository_index(repository_root)

    assert [entry.id for entry in index.packages] == [
        "inbox-productivity",
        "laptop-support",
    ]
    for entry in index.packages:
        distribution = load_distribution(
            repository_root / entry.package_path,
            expected_digest=entry.package_digest,
        )
        assert distribution.manifest.id == entry.id


def test_repository_index_document_does_not_open_unrelated_package_roots(
    repository_root: Path,
) -> None:
    index_path = repository_root / ".well-known/hermes-workflows/index.json"
    payload = json.loads(index_path.read_bytes())
    payload["packages"][0]["packagePath"] = "packages/missing"
    _write_json(index_path, payload)

    index = package_module.load_repository_index_document(repository_root)

    assert [entry.id for entry in index.packages] == [
        "inbox-productivity",
        "laptop-support",
    ]
    assert index.packages[0].package_path == "packages/missing"
    with pytest.raises(WorkflowMarketplaceError) as error:
        load_repository_index(repository_root)
    _assert_code(error, "package_index_invalid")


def test_selected_entry_verifier_reuses_full_repository_metadata_parity(
    repository_root: Path,
) -> None:
    index = package_module.load_repository_index_document(repository_root)
    entry = next(item for item in index.packages if item.id == "laptop-support")
    distribution = load_distribution(
        repository_root / entry.package_path,
        expected_digest=entry.package_digest,
    )

    package_module.verify_indexed_distribution(entry, distribution)

    stale = entry.model_copy(update={"display_name": "Stale display name"})
    with pytest.raises(WorkflowMarketplaceError) as error:
        package_module.verify_indexed_distribution(stale, distribution)
    _assert_code(error, "package_index_invalid")


def test_repository_index_rejects_intermediate_symlink_escape(
    repository_root: Path,
    tmp_path: Path,
) -> None:
    outside_parent = tmp_path / "outside"
    outside = _new_package(outside_parent / "escaped-package", "escaped-package")
    (repository_root / "escape").symlink_to(outside_parent, target_is_directory=True)
    entry = _package_index_entry(outside, outside_parent)
    entry["packagePath"] = "escape/escaped-package"
    _write_index(repository_root, [entry])

    with pytest.raises(WorkflowMarketplaceError) as error:
        load_repository_index(repository_root)

    _assert_code(error, "package_symlink_unsupported")


def test_repository_index_rejects_resolved_package_root_alias_overlap(
    repository_root: Path,
) -> None:
    package_root = repository_root / "packages" / "laptop-support"
    (repository_root / "aliases").symlink_to(
        repository_root / "packages",
        target_is_directory=True,
    )
    original = _package_index_entry(package_root, repository_root)
    alias = {**original, "id": "zz-alias", "packagePath": "aliases/laptop-support"}
    _write_index(repository_root, [original, alias])

    with pytest.raises(WorkflowMarketplaceError) as error:
        load_repository_index(repository_root)

    _assert_code(error, "package_symlink_unsupported")


@pytest.mark.parametrize("relationship", ["same", "nested", "parent"])
def test_resolved_package_roots_must_not_overlap(
    tmp_path: Path,
    relationship: str,
) -> None:
    first = tmp_path / "first"
    child = first / "child"
    child.mkdir(parents=True)
    first_identity = (first.stat().st_dev, first.stat().st_ino)
    child_identity = (child.stat().st_dev, child.stat().st_ino)
    if relationship == "same":
        existing_path = (first_identity,)
        candidate_path = (first_identity,)
    elif relationship == "nested":
        existing_path = (first_identity,)
        candidate_path = (first_identity, child_identity)
    else:
        existing_path = (first_identity, child_identity)
        candidate_path = (first_identity,)

    with pytest.raises(WorkflowMarketplaceError) as error:
        package_module._reject_resolved_root_overlap(
            [existing_path],
            candidate_path,
        )

    _assert_code(error, "package_root_nested")


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        ("metadata", "package_index_invalid"),
        ("digest", "package_digest_mismatch"),
        ("missing_root", "package_index_invalid"),
    ],
)
def test_repository_index_rejects_stale_or_unresolved_claims(
    repository_root: Path,
    mutate: str,
    code: str,
) -> None:
    path = repository_root / ".well-known" / "hermes-workflows" / "index.json"
    index = json.loads(path.read_bytes())
    entry = index["packages"][0]
    if mutate == "metadata":
        entry["displayName"] = "Stale display name"
    elif mutate == "digest":
        entry["packageDigest"] = "0" * 64
    else:
        entry["packagePath"] = "packages/missing"
    _write_json(path, index)

    with pytest.raises(WorkflowMarketplaceError, match=code):
        load_repository_index(repository_root)


def test_repository_index_rejects_nested_package_paths(repository_root: Path) -> None:
    path = repository_root / ".well-known" / "hermes-workflows" / "index.json"
    index = json.loads(path.read_bytes())
    child = dict(index["packages"][0])
    child["id"] = "nested"
    child["packagePath"] = f"{child['packagePath']}/nested"
    index["packages"].append(child)
    index["packages"].sort(key=lambda entry: entry["id"])
    _write_json(path, index)

    with pytest.raises(WorkflowMarketplaceError, match="package_root_nested"):
        load_repository_index(repository_root)


@pytest.mark.parametrize(
    ("offset", "code"),
    [
        (0, None),
        (1, "package_index_size_limit"),
    ],
)
def test_repository_index_byte_limit_is_inclusive(
    repository_root: Path,
    offset: int,
    code: str | None,
) -> None:
    limit = load_package_contract().resource_rules.max_index_bytes
    path = repository_root / ".well-known" / "hermes-workflows" / "index.json"
    payload = path.read_bytes()
    assert len(payload) < limit
    path.write_bytes(payload + b" " * (limit + offset - len(payload)))

    if code is None:
        assert len(load_repository_index(repository_root).packages) == 2
    else:
        with pytest.raises(WorkflowMarketplaceError, match=code):
            load_repository_index(repository_root)


def _write_large_repository_index(root: Path, count: int) -> None:
    packages: list[dict[str, object]] = []
    for index in range(count):
        package_id = f"p{index:04x}"
        package_root = _new_package(root / package_id, package_id)
        packages.append(_package_index_entry(package_root, root))
    _write_json(
        root / ".well-known" / "hermes-workflows" / "index.json",
        {"schemaVersion": 1, "packages": packages},
        compact=True,
    )


def test_repository_catalog_entry_limit_accepts_exact_boundary(tmp_path: Path) -> None:
    limit = load_package_contract().resource_rules.max_catalog_entries
    root = tmp_path / "catalog-exact"
    root.mkdir()
    _write_large_repository_index(root, limit)
    index_path = root / ".well-known" / "hermes-workflows" / "index.json"
    assert (
        index_path.stat().st_size
        <= load_package_contract().resource_rules.max_index_bytes
    )

    assert len(load_repository_index(root).packages) == limit


def test_repository_catalog_entry_limit_rejects_one_over(tmp_path: Path) -> None:
    limit = load_package_contract().resource_rules.max_catalog_entries
    root = tmp_path / "catalog-over"
    root.mkdir()
    packages = []
    for index in range(limit + 1):
        package_id = f"p{index:04x}"
        packages.append({
            "contractVersion": 1,
            "description": "P",
            "displayName": "P",
            "id": package_id,
            "license": "M",
            "packageDigest": "0" * 64,
            "packagePath": package_id,
            "publisher": "P",
            "tags": ["p"],
            "version": "1.0.0",
        })
    _write_json(
        root / ".well-known" / "hermes-workflows" / "index.json",
        {"schemaVersion": 1, "packages": packages},
        compact=True,
    )

    with pytest.raises(WorkflowMarketplaceError, match="package_catalog_entry_limit"):
        load_repository_index(root)


def test_scan_rejects_a_backslash_filename_where_the_host_supports_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "backslash"
    root.mkdir()
    candidate = root / r"unsafe\name.txt"
    try:
        candidate.write_bytes(b"unsafe")
    except OSError:
        pytest.skip("host filesystem does not support backslashes in filenames")

    with pytest.raises(WorkflowMarketplaceError, match="package_path_backslash"):
        scan_package_files(root)


def test_package_scan_does_not_follow_directory_symlinks(
    package_root: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "large.bin").write_bytes(b"outside")
    (package_root / "linked-directory").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkflowMarketplaceError, match="package_symlink_unsupported"):
        load_distribution(package_root)


def test_scan_returns_an_absolute_logical_path_without_resolving_links(
    package_root: Path,
) -> None:
    files = scan_package_files(package_root)

    assert all(item.path.is_absolute() for item in files)
    assert all(
        os.path.commonpath((package_root, item.path)) == str(package_root)
        for item in files
    )


def test_scanner_uses_descriptor_branch_when_supported(
    package_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not package_module._HAS_DESCRIPTOR_WALK:
        pytest.skip("host does not provide descriptor-relative no-follow traversal")
    monkeypatch.setattr(package_module, "_HAS_DESCRIPTOR_WALK", True)

    assert scan_package_files(package_root)


def test_scanner_fails_closed_when_descriptor_safe_traversal_is_unavailable(
    package_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(package_module, "_HAS_DESCRIPTOR_WALK", False)

    with pytest.raises(WorkflowMarketplaceError) as error:
        scan_package_files(package_root)

    _assert_code(error, "package_digest_mismatch")
    assert str(package_root) not in str(error.value)


@pytest.mark.parametrize("mutation", ["disappear", "replace"])
def test_package_root_recheck_failures_use_stable_redacted_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    root = tmp_path / "changing-package"
    root.mkdir()
    (root / "file.bin").write_bytes(b"content")
    moved = tmp_path / "moved-package"
    real_lstat = Path.lstat
    root_checks = 0

    def mutate_before_recheck(path: Path):
        nonlocal root_checks
        if path == root:
            root_checks += 1
            if root_checks == 2:
                path.rename(moved)
                if mutation == "replace":
                    path.mkdir()
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", mutate_before_recheck)

    with pytest.raises(WorkflowMarketplaceError) as error:
        scan_package_files(root)

    _assert_code(error, "package_digest_mismatch")
    assert str(root) not in str(error.value)


@pytest.mark.parametrize("target", ["component", "root"])
@pytest.mark.parametrize("mutation", ["disappear", "replace"])
def test_repository_recheck_failures_use_stable_redacted_diagnostic(
    repository_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    target: str,
) -> None:
    index_parent = repository_root / ".well-known"
    changing = index_parent if target == "component" else repository_root
    moved = tmp_path / f"moved-index-{target}"
    real_read_file_at = package_module._read_file_at

    def mutate_after_index_read(*args, **kwargs):
        content = real_read_file_at(*args, **kwargs)
        if args[3] == ".well-known/hermes-workflows/index.json":
            changing.rename(moved)
            if mutation == "replace":
                changing.mkdir()
        return content

    monkeypatch.setattr(package_module, "_read_file_at", mutate_after_index_read)

    with pytest.raises(WorkflowMarketplaceError) as error:
        load_repository_index(repository_root)

    _assert_code(error, "package_index_invalid")
    assert str(repository_root) not in str(error.value)


@pytest.mark.parametrize("mutation", ["disappear", "replace"])
def test_file_recheck_failures_use_stable_redacted_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    root = tmp_path / "file-recheck"
    root.mkdir()
    target = root / "changing.bin"
    target.write_bytes(b"content")
    moved = root / "moved.bin"
    real_read_descriptor = package_module._read_descriptor
    mutated = False

    def mutate_after_read(descriptor: int, expected_size: int) -> bytes:
        nonlocal mutated
        content = real_read_descriptor(descriptor, expected_size)
        if not mutated:
            mutated = True
            target.rename(moved)
            if mutation == "replace":
                target.write_bytes(b"replacement")
        return content

    monkeypatch.setattr(package_module, "_read_descriptor", mutate_after_read)

    with pytest.raises(WorkflowMarketplaceError) as error:
        scan_package_files(root)

    _assert_code(error, "package_digest_mismatch")
    assert str(target) not in str(error.value)


@pytest.mark.parametrize("mutation", ["disappear", "replace"])
def test_directory_recheck_failures_use_stable_redacted_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    root = tmp_path / "directory-recheck"
    child = root / "child"
    child.mkdir(parents=True)
    (child / "file.bin").write_bytes(b"content")
    moved = root / "moved-child"
    real_read_file_at = package_module._read_file_at

    def mutate_after_child_read(*args, **kwargs):
        content = real_read_file_at(*args, **kwargs)
        if args[3] == "child/file.bin":
            child.rename(moved)
            if mutation == "replace":
                child.mkdir()
        return content

    monkeypatch.setattr(package_module, "_read_file_at", mutate_after_child_read)

    with pytest.raises(WorkflowMarketplaceError) as error:
        scan_package_files(root)

    _assert_code(error, "package_digest_mismatch")
    assert str(child) not in str(error.value)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schemaVersion":1,"schemaVersion":1}',
        b'{"value":NaN}',
        b"\xff\xfe\x80",
    ],
    ids=["duplicate-key", "non-finite", "invalid-utf8"],
)
@pytest.mark.parametrize(
    ("document", "code"),
    [
        ("manifest", "package_manifest_invalid"),
        ("digests", "package_digest_invalid"),
        ("index", "package_index_invalid"),
    ],
)
def test_untrusted_json_rejects_ambiguous_or_invalid_bytes(
    repository_root: Path,
    payload: bytes,
    document: str,
    code: str,
) -> None:
    if document == "manifest":
        target = (
            repository_root / "packages" / "laptop-support" / "workflow-package.json"
        )
        load = lambda: load_distribution(target.parent)
    elif document == "digests":
        target = repository_root / "packages" / "laptop-support" / "digests.json"
        load = lambda: load_distribution(target.parent)
    else:
        target = repository_root / ".well-known" / "hermes-workflows" / "index.json"
        load = lambda: load_repository_index(repository_root)
    target.write_bytes(payload)

    with pytest.raises(WorkflowMarketplaceError) as error:
        load()

    _assert_code(error, code)
    assert str(target) not in str(error.value)
