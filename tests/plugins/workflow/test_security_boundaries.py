from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shutil
import stat

import pytest

import plugins.workflow.sessions as sessions_module
from plugins.workflow.showcase import ShowcaseCatalogError, load_showcase_catalog
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.evidence import EvidenceReader
import plugins.workflow.evidence as evidence_module
from plugins.workflow.store import RunStore
from plugins.workflow.trust import WorkflowTrustStore, compute_package_digest
from plugins.workflow.schema import load_workflow
from plugins.workflow.sessions import (
    TypedMirrorIntegrityError,
    TypedMirrorObligation,
    TypedMirrorStore,
)


SHOWCASES = Path(__file__).parents[3] / "plugins/workflow/showcases"


class _ReparseStat:
    def __init__(self, observed: object) -> None:
        self._observed = observed
        self.st_file_attributes = getattr(
            stat,
            "FILE_ATTRIBUTE_REPARSE_POINT",
            0x400,
        )

    def __getattr__(self, name: str) -> object:
        return getattr(self._observed, name)


def _inject_reparse(monkeypatch, target: Path) -> None:
    original_lstat = Path.lstat

    def injected(path: Path):
        observed = original_lstat(path)
        if path == target:
            return _ReparseStat(observed)
        return observed

    monkeypatch.setattr(Path, "lstat", injected)


def _inject_descriptor_reparse(monkeypatch, target: Path) -> None:
    original_stat = sessions_module.os.stat

    def injected(path, *, dir_fd=None, follow_symlinks=True):
        observed = original_stat(
            path,
            dir_fd=dir_fd,
            follow_symlinks=follow_symlinks,
        )
        if dir_fd is not None and str(path) == target.name:
            return _ReparseStat(observed)
        return observed

    monkeypatch.setattr(sessions_module.os, "stat", injected)


def _swap_directory_to_external(
    directory: Path,
    external: Path,
) -> Path:
    retained = directory.with_name(f"{directory.name}-retained")
    directory.rename(retained)
    external.mkdir()
    directory.symlink_to(external, target_is_directory=True)
    return retained


def _log_path(tmp_path, workflow_writer, *, name: str):
    package = load_workflow(workflow_writer(tmp_path / "package", name=name))
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key=f"{name}-intent",
            concurrency_key=name,
        ),
        immutable_snapshot=prepared,
    )
    stdout = (
        store.run_directory(admitted.run_id)
        / "nodes"
        / "n1"
        / "a1"
        / "stdout.txt"
    )
    stdout.parent.mkdir(parents=True)
    return store, admitted.run_id, stdout


@pytest.mark.parametrize(
    "relative",
    [
        "catalog.yaml",
        "digests.json",
        "packages/laptop-diagnostic/fixtures/laptop-snapshot.json",
        "packages/laptop-diagnostic/scripts/analyze-snapshot.py",
        "packages/laptop-diagnostic/commands/interpret-report.md",
        "packages/laptop-diagnostic/workflows/laptop-diagnostic.yaml",
        "packages/laptop-diagnostic/workflows/laptop-diagnostic.hermes.yaml",
        "packages/ai-extensions/mcp/echo.yaml",
        "packages/ai-extensions/mcp/echo-server.py",
    ],
)
def test_every_showcase_resource_class_fails_closed_when_tampered(
    tmp_path: Path, relative: str
) -> None:
    copied = tmp_path / "showcases"
    shutil.copytree(SHOWCASES, copied)
    target = copied / relative
    target.write_bytes(target.read_bytes() + b"\n# tampered\n")

    with pytest.raises(ShowcaseCatalogError):
        load_showcase_catalog(copied)


def test_distribution_identity_does_not_trust_a_changed_executable(tmp_path: Path) -> None:
    workflow = SHOWCASES / "packages/resilience/workflows/resilience.yaml"
    package = load_workflow(workflow)
    before = compute_package_digest(package).sha256
    store = WorkflowTrustStore(tmp_path)
    store.trust(before, actor="trusted_distribution", risk_digest=before)

    copied = tmp_path / "package"
    shutil.copytree(package.root, copied)
    changed = copied / "scripts/fail-once.py"
    changed.write_text(changed.read_text() + "\n# changed\n")
    after = compute_package_digest(load_workflow(copied / "workflows/resilience.yaml")).sha256

    assert after != before
    assert store.check(after, risk_digest=after) == "untrusted"
    records = json.loads(store.path.read_text())["records"]
    assert records[before]["actor"] == "trusted_distribution"


def test_even_digest_consistent_bundle_rejects_live_inventory_commands(
    tmp_path: Path,
) -> None:
    copied = tmp_path / "showcases"
    shutil.copytree(SHOWCASES, copied)
    script = copied / "packages/laptop-diagnostic/scripts/analyze-snapshot.py"
    script.write_text(script.read_text() + "\n# powershell Get-ComputerInfo\n")

    package = copied / "packages/laptop-diagnostic"
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            relative = path.relative_to(package).as_posix()
            digest.update(relative.encode())
            digest.update(b"\0")
            digest.update(str(len(data)).encode())
            digest.update(b"\0")
            digest.update(data)
            digest.update(b"\0")
    manifest_path = copied / "digests.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["packages"]["laptop-diagnostic"] = digest.hexdigest()
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ShowcaseCatalogError, match="safety"):
        load_showcase_catalog(copied)


@pytest.mark.skipif(
    not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd,
    reason="descriptor-relative no-follow reads require POSIX openat",
)
def test_log_evidence_rejects_replace_between_enumeration_and_open(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    store, run_id, stdout = _log_path(tmp_path, workflow_writer, name="log-race")
    stdout.write_text("SAFE", encoding="utf-8")
    secret = tmp_path / "race-secret"
    secret.write_text("RACE_ESCAPE_SENTINEL", encoding="utf-8")
    original_read_bytes = Path.read_bytes
    original_os_open = os.open
    swapped = False

    def swap_candidate() -> None:
        nonlocal swapped
        if swapped:
            return
        swapped = True
        stdout.unlink()
        stdout.symlink_to(secret)

    def racing_read_bytes(path: Path) -> bytes:
        if path == stdout:
            swap_candidate()
        return original_read_bytes(path)

    def racing_os_open(path, flags, mode=0o777, *, dir_fd=None):
        if str(path) == stdout.name and dir_fd is not None:
            swap_candidate()
        return original_os_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(Path, "read_bytes", racing_read_bytes)
    if hasattr(evidence_module, "os"):
        monkeypatch.setattr(evidence_module.os, "open", racing_os_open)

    page = EvidenceReader(store).query(run_id, kind="logs")

    assert swapped is True
    assert "RACE_ESCAPE_SENTINEL" not in str(page)
    assert page["items"] == []
    assert page["warnings"] == ["unsafe_evidence_path"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO is POSIX-specific")
def test_log_evidence_rejects_non_regular_fifo(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    store, run_id, stdout = _log_path(tmp_path, workflow_writer, name="log-fifo")
    os.mkfifo(stdout)
    original_read_bytes = Path.read_bytes

    def nonblocking_legacy_read(path: Path) -> bytes:
        if path == stdout:
            return b"NON_REGULAR_SENTINEL"
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", nonblocking_legacy_read)

    page = EvidenceReader(store).query(run_id, kind="logs")

    assert "NON_REGULAR_SENTINEL" not in str(page)
    assert page["items"] == []
    assert page["warnings"] == ["unsafe_evidence_path"]


def _mirror_obligation(
    data: bytes,
    *,
    run_id: str = "run-1",
    operator_scope: str = "scope",
):
    digest = hashlib.sha256(data).hexdigest()
    return TypedMirrorObligation(
        mirror_id=hashlib.sha256(f"mirror:{run_id}".encode()).hexdigest(),
        workflow="workflow",
        node_id="node",
        operator_scope=operator_scope,
        run_id=run_id,
        attempt_id="attempt-1",
        publication_id="a" * 32,
        content_name="content.md",
        output_type="Report",
        media_type="text/markdown; charset=utf-8",
        size_bytes=len(data),
        sha256=digest,
    )


def test_typed_mirror_storage_is_profile_isolated_and_hides_unverified_index(
    tmp_path
) -> None:
    data = b"profile one"
    first = TypedMirrorStore(tmp_path / "profile-one")
    second = TypedMirrorStore(tmp_path / "profile-two")
    obligation = _mirror_obligation(data)

    staged = first.stage(obligation, data)

    assert first.get("workflow", "node", "scope") is None
    completed = first.activate(staged)

    assert first.get("workflow", "node", "scope") == completed
    assert second.get("workflow", "node", "scope") is None
    content = (
        tmp_path
        / "profile-one"
        / "workflows"
        / "typed-mirrors"
        / "content"
        / obligation.sha256
    )
    content.write_bytes(b"same-size-bad")
    assert first.get("workflow", "node", "scope") is None


def test_typed_mirror_content_symlink_never_reaches_outside_profile(tmp_path) -> None:
    data = b"safe"
    mirrors = TypedMirrorStore(tmp_path / "profile")
    obligation = _mirror_obligation(data)
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    content = mirrors.root / "content" / obligation.sha256
    content.parent.mkdir(parents=True, exist_ok=True)
    content.symlink_to(outside)

    with pytest.raises(TypedMirrorIntegrityError):
        mirrors.complete(obligation, data)

    assert outside.read_bytes() == b"keep"


def test_typed_mirror_directory_reparse_point_fails_closed(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "profile"
    mirrors = TypedMirrorStore(home)
    sentinel = tmp_path / "outside-directory-sentinel"
    sentinel.write_bytes(b"keep")
    _inject_reparse(monkeypatch, mirrors.root)
    original_mkdir = Path.mkdir

    def trapped_descendant_mkdir(path: Path, *args, **kwargs):
        if mirrors.root in path.parents:
            sentinel.write_bytes(b"continued through unsafe directory")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", trapped_descendant_mkdir)

    with pytest.raises(TypedMirrorIntegrityError, match="directory is unsafe"):
        TypedMirrorStore(home)

    assert sentinel.read_bytes() == b"keep"


def test_typed_mirror_content_reparse_point_fails_closed(
    tmp_path,
    monkeypatch,
) -> None:
    data = b"safe"
    mirrors = TypedMirrorStore(tmp_path / "profile")
    obligation = _mirror_obligation(data)
    mirrors.stage(obligation, data)
    content = mirrors.content_root / obligation.sha256
    sentinel = tmp_path / "outside-content-sentinel"
    sentinel.write_bytes(b"keep")
    _inject_descriptor_reparse(monkeypatch, content)
    original_link = os.link

    def trapped_content_link(source, destination, *args, **kwargs):
        if str(destination) == content.name:
            sentinel.write_bytes(b"continued through unsafe content")
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "link", trapped_content_link)

    with pytest.raises(TypedMirrorIntegrityError, match="file is unsafe"):
        mirrors.stage(obligation, data)

    assert sentinel.read_bytes() == b"keep"


def test_typed_mirror_index_reparse_point_is_invisible(
    tmp_path,
    monkeypatch,
) -> None:
    data = b"safe"
    mirrors = TypedMirrorStore(tmp_path / "profile")
    obligation = _mirror_obligation(data)
    mirrors.complete(obligation, data)
    index = mirrors.index_root / mirrors._scope_id("workflow", "node", "scope")
    index = index.with_suffix(".json")
    sentinel = tmp_path / "outside-index-sentinel"
    sentinel.write_bytes(b"keep")
    _inject_descriptor_reparse(monkeypatch, index)
    original_open = os.open

    def trapped_index_open(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None and str(path) == index.name:
            sentinel.write_bytes(b"followed unsafe index")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", trapped_index_open)

    assert mirrors.get("workflow", "node", "scope") is None
    assert sentinel.read_bytes() == b"keep"


def test_typed_mirror_completion_rejects_reparse_index_before_replace(
    tmp_path,
    monkeypatch,
) -> None:
    mirrors = TypedMirrorStore(tmp_path / "profile")
    first_data = b"first"
    mirrors.complete(_mirror_obligation(first_data), first_data)
    index = mirrors.index_root / mirrors._scope_id("workflow", "node", "scope")
    index = index.with_suffix(".json")
    original_index = index.read_bytes()
    sentinel = tmp_path / "outside-index-write-sentinel"
    sentinel.write_bytes(b"keep")
    _inject_descriptor_reparse(monkeypatch, index)
    original_replace = os.replace

    def trapped_index_replace(
        source,
        destination,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
    ):
        if dst_dir_fd is not None and str(destination) == index.name:
            sentinel.write_bytes(b"replaced unsafe index")
        return original_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "replace", trapped_index_replace)
    second_data = b"second"

    with pytest.raises(TypedMirrorIntegrityError, match="file is unsafe"):
        mirrors.complete(
            _mirror_obligation(second_data, run_id="run-2"),
            second_data,
        )

    assert index.read_bytes() == original_index
    assert sentinel.read_bytes() == b"keep"


@pytest.mark.parametrize(
    "branch",
    ["root", "content", "entries", "activations", "indexes"],
)
def test_typed_mirror_operations_reject_swapped_parent_directories(
    tmp_path,
    branch: str,
) -> None:
    mirrors = TypedMirrorStore(tmp_path / "profile")
    data = b"anchored mirror"
    obligation = _mirror_obligation(data)
    record = mirrors.stage(obligation, data)
    target = {
        "root": mirrors.root,
        "content": mirrors.content_root,
        "entries": mirrors.entry_root,
        "activations": mirrors.activation_root,
        "indexes": mirrors.index_root,
    }[branch]
    external = tmp_path / f"outside-{branch}"
    retained = _swap_directory_to_external(target, external)
    sentinel = external / "sentinel"
    sentinel.write_bytes(b"keep")

    if branch == "root":
        for name in ("content", "entries", "activations", "indexes"):
            (external / name).mkdir()
        action = lambda: mirrors.point(record)
    elif branch == "content":
        next_data = b"different content"
        action = lambda: mirrors.stage(
            _mirror_obligation(next_data, run_id="run-2"),
            next_data,
        )
    elif branch == "entries":
        next_obligation = _mirror_obligation(data, run_id="run-2")
        action = lambda: mirrors.stage(next_obligation, data)
    elif branch == "activations":
        action = lambda: mirrors.verify(record)
    else:
        action = lambda: mirrors.point(record)

    with pytest.raises(TypedMirrorIntegrityError):
        action()

    assert sentinel.read_bytes() == b"keep"
    assert {path.name for path in external.iterdir()} <= {
        "sentinel",
        "content",
        "entries",
        "activations",
        "indexes",
    }
    assert retained.is_dir()


def test_typed_mirror_store_fails_closed_without_descriptor_relative_io(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(sessions_module.os, "supports_dir_fd", set())

    with pytest.raises(TypedMirrorIntegrityError, match="unavailable"):
        TypedMirrorStore(tmp_path / "profile")


def test_completed_mirror_recovery_replaces_a_pending_current_pointer(tmp_path) -> None:
    mirrors = TypedMirrorStore(tmp_path / "profile")
    first_data = b"completed-a"
    first = mirrors.complete(_mirror_obligation(first_data), first_data)
    pending_data = b"pending-b"
    pending = mirrors.stage(
        _mirror_obligation(pending_data, run_id="run-2"),
        pending_data,
    )
    assert mirrors.point(pending)
    assert mirrors.get("workflow", "node", "scope") is None

    assert mirrors.point(first, replace_current=False)
    mirrors.verify(first)

    assert mirrors.get("workflow", "node", "scope") == first


def test_completed_mirror_recovery_replaces_an_activated_cross_scope_index(
    tmp_path,
) -> None:
    mirrors = TypedMirrorStore(tmp_path / "profile")
    first_data = b"completed-a"
    first = mirrors.complete(
        _mirror_obligation(first_data, operator_scope="scope-a"),
        first_data,
    )
    second_data = b"completed-b"
    second = mirrors.complete(
        _mirror_obligation(
            second_data,
            run_id="run-2",
            operator_scope="scope-b",
        ),
        second_data,
    )
    first_index = mirrors.index_root / (
        mirrors._scope_id("workflow", "node", "scope-a") + ".json"
    )
    second_index = mirrors.index_root / (
        mirrors._scope_id("workflow", "node", "scope-b") + ".json"
    )
    first_index.write_bytes(second_index.read_bytes())
    assert mirrors.get("workflow", "node", "scope-a") is None

    assert mirrors.point(first, replace_current=False)
    mirrors.verify(first)

    assert mirrors.get("workflow", "node", "scope-a") == first
    assert mirrors.get("workflow", "node", "scope-b") == second
    assert mirrors.list_history("workflow", "node", "scope-b") == (second,)


@pytest.mark.parametrize("malformation", ["same_entry_missing_fields", "negative_generation"])
def test_typed_mirror_point_repairs_malformed_scope_indexes(
    tmp_path,
    malformation: str,
) -> None:
    mirrors = TypedMirrorStore(tmp_path / "profile")
    first_data = b"first"
    first = mirrors.stage(_mirror_obligation(first_data), first_data)
    target_data = b"target"
    target = mirrors.stage(
        _mirror_obligation(target_data, run_id="run-2"),
        target_data,
    )
    index = mirrors.index_root / mirrors._scope_id("workflow", "node", "scope")
    index = index.with_suffix(".json")
    if malformation == "same_entry_missing_fields":
        index.write_text(json.dumps({"entry_id": target.entry_id}), encoding="utf-8")
    else:
        index.write_text(
            json.dumps({
                "schema_version": 1,
                "generation": -5,
                "entry_id": first.entry_id,
                "updated_at": "2026-07-30T12:00:00+00:00",
            }),
            encoding="utf-8",
        )

    assert mirrors.point(target)
    mirrors.verify(target)

    repaired = json.loads(index.read_bytes())
    assert repaired["generation"] == 1
    assert mirrors.get("workflow", "node", "scope") == target
