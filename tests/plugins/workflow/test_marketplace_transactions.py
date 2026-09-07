"""Two-phase and recoverable workflow marketplace transaction contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
from typing import cast, Literal

import pytest

from plugins.workflow.locks import workflow_lock
from plugins.workflow.marketplace.models import InstalledPackageIdentity
from plugins.workflow.marketplace.package import (
    WorkflowMarketplaceError,
    load_distribution,
)
from plugins.workflow.marketplace.provenance import InstalledPackageStore
from plugins.workflow.marketplace.transactions import (
    MarketplaceTransactionStore,
    PreparedTransaction,
    TransactionCandidate,
)
from plugins.workflow.trust import WorkflowTrustStore
from plugins.workflow.store import RunStore


FIXTURE_PACKAGES = (
    Path(__file__).parent / "fixtures" / "marketplace" / "repository" / "packages"
)
REVIEW = "b" * 64
_DIGEST_DOMAIN = b"hermes.workflow-package.v1\0"


class SimulatedCrash(BaseException):
    pass


def _publish(root: Path) -> str:
    files = [
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and path.relative_to(root).as_posix() != "digests.json"
    ]
    digest = hashlib.sha256(_DIGEST_DOMAIN)
    records: list[dict[str, object]] = []
    for relative_path, content in sorted(files):
        path_bytes = relative_path.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        file_digest = hashlib.sha256(content)
        digest.update(file_digest.digest())
        records.append({
            "path": relative_path,
            "sha256": file_digest.hexdigest(),
            "size": len(content),
        })
    package_digest = digest.hexdigest()
    (root / "digests.json").write_text(
        json.dumps(
            {
                "algorithm": "sha256",
                "contractVersion": 1,
                "files": records,
                "packageDigest": package_digest,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return package_digest


def _package(tmp_path: Path, version: str, *, suffix: str = "") -> Path:
    root = tmp_path / f"candidate-{version}-{suffix or 'base'}"
    shutil.copytree(FIXTURE_PACKAGES / "laptop-support", root)
    manifest_path = root / "workflow-package.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["version"] = version
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "fixtures" / "sample.json").write_text(
        json.dumps({"version": version, "suffix": suffix}) + "\n",
        encoding="utf-8",
    )
    _publish(root)
    return root


def _candidate(
    home: Path,
    root: Path,
    *,
    source_key: str = "company",
    source_name: str = "company",
    operation: Literal["install", "remove"] = "install",
    installed_provenance=None,
) -> TransactionCandidate:
    distribution = load_distribution(root)
    return TransactionCandidate(
        distribution=distribution,
        identity=InstalledPackageIdentity(
            sourceKey=source_key,
            packageId=distribution.manifest.id,
        ),
        source_name=source_name,
        repository_url="https://github.com/example/workflows.git",
        configured_ref="release",
        resolved_commit=("a" if distribution.manifest.version == "1.0.0" else "c") * 40,
        package_path="packages/laptop-support",
        destination=InstalledPackageStore(home).package_root(
            InstalledPackageIdentity(
                sourceKey=source_key,
                packageId=distribution.manifest.id,
            )
        ),
        operation=operation,
        installed_provenance=installed_provenance,
    )


def _snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def _consume(
    store: MarketplaceTransactionStore,
    candidate: TransactionCandidate,
    *,
    actor: str = "alice",
    profile: str = "p1",
):
    prepared = store.prepare(
        candidate,
        review_digest=REVIEW,
        actor=actor,
        profile=profile,
    )
    return store.consume(prepared.token, actor=actor, profile=profile)


def _install(
    store: MarketplaceTransactionStore,
    candidate: TransactionCandidate,
):
    return store.atomic_install(_consume(store, candidate), review_digest=REVIEW)


def _removal_candidate(
    store: MarketplaceTransactionStore,
    installed,
) -> TransactionCandidate:
    destination = store.installed_store.package_root(installed.identity)
    return _candidate(
        store.home,
        destination,
        source_key=installed.identity.source_key,
        source_name=installed.source_name,
        operation="remove",
        installed_provenance=installed,
    )


def _authorize_remove(
    store: MarketplaceTransactionStore,
    installed,
):
    candidate = _removal_candidate(store, installed)
    return candidate, _consume(store, candidate)


def test_fresh_home_preparation_uses_private_marketplace_workspaces(tmp_path):
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    prepared = store.prepare(
        candidate, review_digest=REVIEW, actor="alice", profile="p1"
    )
    marketplace_root = tmp_path / "marketplace" / "workflows"
    assert store.staging_root == marketplace_root / ".staging"
    assert store.quarantine_root == marketplace_root / ".quarantine"
    assert prepared.staging_path == (
        marketplace_root / ".staging" / prepared.transaction_id / "package"
    )
    if os.name != "nt":
        for directory in (
            marketplace_root,
            store.staging_root,
            store.quarantine_root,
            prepared.staging_path.parent,
        ):
            assert directory.stat().st_mode & 0o777 == 0o700
        assert (
            prepared.staging_path.parent / "owner.json"
        ).stat().st_mode & 0o777 == 0o600
    installed = store.atomic_install(
        store.consume(prepared.token, actor="alice", profile="p1"),
        review_digest=REVIEW,
    )
    assert installed.distribution_digest == candidate.distribution.digest
    assert not (tmp_path / "workflows" / ".staging").exists()
    assert not (tmp_path / "workflows" / ".quarantine").exists()


def test_fresh_home_recovery_establishes_private_marketplace_authority(tmp_path):
    store = MarketplaceTransactionStore(tmp_path)
    assert store.recover_transactions() == ()
    assert (tmp_path / "marketplace" / "workflows" / ".staging").is_dir()
    assert (tmp_path / "marketplace" / "workflows" / ".quarantine").is_dir()
    assert not (tmp_path / "workflows" / ".staging").exists()


@pytest.mark.parametrize(
    "root_name", ["marketplace", "workflows", ".staging", ".quarantine"]
)
@pytest.mark.parametrize("damage", ["symlink", "public_mode", "file"])
@pytest.mark.parametrize("entry_point", ["prepare", "recover"])
def test_workspace_creation_refuses_hostile_marketplace_parent(
    tmp_path, root_name, damage, entry_point
):
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    store._shared_store._ensure_private_root()
    store._ensure_workflow_roots()
    root = {
        "marketplace": store.root.parent,
        "workflows": store.root,
        ".staging": store.root / ".staging",
        ".quarantine": store.root / ".quarantine",
    }[root_name]
    if damage == "public_mode":
        root.chmod(0o755)
    else:
        target = tmp_path / "foreign-root"
        root.rename(target)
        if damage == "symlink":
            root.symlink_to(target, target_is_directory=True)
        else:
            root.write_bytes(b"foreign parent file")
    before = _snapshot(tmp_path)
    malformed_authority = damage == "file" and root_name in {"marketplace", "workflows"}
    if damage == "public_mode" and os.name == "nt":
        # Native Windows does not enforce the POSIX directory mode policy.
        assert store.recover_transactions() == ()
    else:
        # Preserve the shared authority's existing native mkdir refusal for a
        # regular-file parent, without broadening its error contract here.
        expected_error = (
            FileExistsError if malformed_authority else WorkflowMarketplaceError
        )
        with pytest.raises(expected_error) as error:
            if entry_point == "recover":
                store.recover_transactions()
            else:
                store.prepare(
                    candidate, review_digest=REVIEW, actor="alice", profile="p1"
                )
        if not malformed_authority:
            assert isinstance(error.value, WorkflowMarketplaceError)
            assert error.value.code == (
                "transaction_state_invalid"
                if root_name in {"marketplace", "workflows"}
                else "transaction_destination_invalid"
            )
    assert _snapshot(tmp_path) == before
    if damage == "symlink":
        assert root.is_symlink()
    elif damage == "public_mode" and os.name != "nt":
        assert root.stat().st_mode & 0o777 == 0o755


def test_private_root_identity_is_rechecked_after_workspace_creation(
    tmp_path, monkeypatch
):
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    store._shared_store._ensure_private_root()
    original = store._shared_store._ensure_private_root
    displaced = tmp_path / "displaced-private-root"

    def replace_after_validation():
        identity = original()
        if store.staging_root.exists() and not displaced.exists():
            store.root.rename(displaced)
            store.root.mkdir(mode=0o700)
        return identity

    monkeypatch.setattr(
        store._shared_store, "_ensure_private_root", replace_after_validation
    )
    with pytest.raises(WorkflowMarketplaceError) as error:
        store.prepare(candidate, review_digest=REVIEW, actor="alice", profile="p1")
    assert error.value.code == "transaction_state_invalid"
    assert not candidate.destination.exists()
    assert not store.path.exists()
    assert not store.installed_store.path.exists()


def _forbid_workspace_enumeration(monkeypatch, roots):
    original_scandir = os.scandir
    original_iterdir = Path.iterdir

    def check(path):
        if isinstance(path, (str, bytes, os.PathLike)):
            candidate = Path(os.fsdecode(path))
            assert not any(
                candidate == root or root in candidate.parents for root in roots
            ), f"cross-authority workspace enumeration: {candidate}"

    def scandir(path):
        check(path)
        return original_scandir(path)

    def iterdir(path):
        check(path)
        return original_iterdir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    monkeypatch.setattr(Path, "iterdir", iterdir)


def test_run_reconciliation_never_enumerates_live_marketplace_envelopes(
    tmp_path, monkeypatch
):
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    prepared = store.prepare(
        candidate, review_digest=REVIEW, actor="alice", profile="p1"
    )
    before = _snapshot(store.staging_root)
    identity = prepared.staging_path.parent.stat()
    with monkeypatch.context() as guard:
        _forbid_workspace_enumeration(
            guard, (store.staging_root, store.quarantine_root)
        )
        runs = RunStore(tmp_path)
        RunStore(tmp_path)
    assert runs.staging_root == tmp_path / "workflows" / ".staging"
    assert runs.quarantine_root == tmp_path / "workflows" / ".quarantine"
    assert _snapshot(store.staging_root) == before
    after = prepared.staging_path.parent.stat()
    assert (after.st_dev, after.st_ino, after.st_mode) == (
        identity.st_dev,
        identity.st_ino,
        identity.st_mode,
    )
    assert not runs.list_admission_events()
    assert list(runs.quarantine_root.iterdir()) == []
    installed = store.atomic_install(
        store.consume(prepared.token, actor="alice", profile="p1"), review_digest=REVIEW
    )
    assert installed.distribution_digest == candidate.distribution.digest


def test_marketplace_recovery_never_enumerates_run_snapshots(tmp_path, monkeypatch):
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    prepared = store.prepare(
        candidate, review_digest=REVIEW, actor="alice", profile="p1"
    )
    with store._locked() as identity:
        store._write_prepared([], parent_identity=identity)
    runs = RunStore(tmp_path)
    for root in (runs.staging_root, runs.quarantine_root):
        snapshot = root / ("e" * 32)
        snapshot.mkdir()
        (snapshot / ".snapshot-owner.json").write_text(
            json.dumps({
                "pid": os.getpid(),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }),
            encoding="utf-8",
        )
        (snapshot / "evidence").write_bytes(b"run snapshot")
    before = (_snapshot(runs.staging_root), _snapshot(runs.quarantine_root))
    modes = (runs.staging_root.stat().st_mode, runs.quarantine_root.stat().st_mode)
    with monkeypatch.context() as guard:
        _forbid_workspace_enumeration(guard, (runs.staging_root, runs.quarantine_root))
        restarted = MarketplaceTransactionStore(tmp_path)
        assert [
            entry.transaction_id for entry in restarted.inspect_recovery().entries
        ] == [prepared.transaction_id]
        assert restarted.recover_transactions() == ()
        assert restarted.inspect_recovery().entries == ()
    assert not prepared.staging_path.parent.exists()
    assert (_snapshot(runs.staging_root), _snapshot(runs.quarantine_root)) == before
    assert (
        runs.staging_root.stat().st_mode,
        runs.quarantine_root.stat().st_mode,
    ) == modes


@pytest.mark.parametrize("operation", ["install", "update", "remove"])
def test_restart_journal_recovery_uses_only_marketplace_workspaces(
    tmp_path, operation, monkeypatch
):
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    if operation in {"update", "remove"}:
        installed = _install(store, candidate)
    if operation == "update":
        candidate = _candidate(tmp_path, _package(tmp_path, "2.0.0"))
    if operation == "remove":
        _, prepared = _authorize_remove(store, installed)
    else:
        prepared = _consume(store, candidate)

    def crash(point):
        if point == (
            "after_provenance_remove"
            if operation == "remove"
            else "after_provenance_write"
        ):
            raise SimulatedCrash(point)

    with pytest.raises(SimulatedCrash):
        mutation = (
            store.atomic_remove if operation == "remove" else store.atomic_install
        )
        mutation(prepared, review_digest=REVIEW, fault=crash)
    journal = store.list_journals()[0]
    if os.name != "nt":
        for package in (journal.staging_path, journal.quarantine_path):
            if package.parent.exists():
                assert package.parent.stat().st_mode & 0o777 == 0o700
                assert (package.parent / "owner.json").stat().st_mode & 0o777 == 0o600
    assert (
        journal.staging_path
        == tmp_path
        / "marketplace"
        / "workflows"
        / ".staging"
        / prepared.transaction_id
        / "package"
    )
    assert (
        journal.quarantine_path
        == tmp_path
        / "marketplace"
        / "workflows"
        / ".quarantine"
        / prepared.transaction_id
        / "package"
    )
    runs = RunStore(tmp_path)
    with monkeypatch.context() as guard:
        _forbid_workspace_enumeration(guard, (runs.staging_root, runs.quarantine_root))
        restarted = MarketplaceTransactionStore(tmp_path)
        assert restarted.recover_transactions() == (prepared.transaction_id,)
        assert restarted.recover_transactions() == ()
        assert restarted.list_journals() == ()
    if operation == "remove":
        assert not candidate.destination.exists()
    else:
        assert (
            load_distribution(candidate.destination).digest
            == candidate.distribution.digest
        )
    assert list(store.staging_root.iterdir()) == []
    assert list(store.quarantine_root.iterdir()) == []


@pytest.mark.parametrize(
    "state_kind", ["prepared", "staging_journal", "quarantine_journal"]
)
def test_pre_release_shared_paths_fail_closed_without_migration(tmp_path, state_kind):
    store = MarketplaceTransactionStore(tmp_path)
    first = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    _install(store, first)
    store.trust_store.trust("d" * 64, actor="manual", risk_digest="e" * 64)
    second = _candidate(tmp_path, _package(tmp_path, "2.0.0"))
    prepared = store.prepare(second, review_digest=REVIEW, actor="alice", profile="p1")
    path, key, field = store.path, "transactions", "stagingPath"
    if state_kind != "prepared":
        consumed = store.consume(prepared.token, actor="alice", profile="p1")

        def crash(point):
            if point == "after_backup_move":
                raise SimulatedCrash(point)

        with pytest.raises(SimulatedCrash):
            store.atomic_install(consumed, review_digest=REVIEW, fault=crash)
        path, key = store.journal_path, "journals"
        if state_kind == "quarantine_journal":
            field = "quarantinePath"
    state = json.loads(path.read_bytes())
    record = state[key][0]
    old_envelope = Path(record[field]).parent
    legacy_root = (
        tmp_path
        / "workflows"
        / (".quarantine" if field == "quarantinePath" else ".staging")
    )
    legacy_root.mkdir(mode=0o700, exist_ok=True)
    legacy_envelope = legacy_root / prepared.transaction_id
    if old_envelope != legacy_envelope:
        old_envelope.rename(legacy_envelope)
    record[field] = str(legacy_envelope / "package")
    if state_kind == "prepared":
        binding = {
            key: value
            for key, value in record.items()
            if key not in {"tokenDigest", "confirmationDigest"}
        }
        encoded = json.dumps(
            binding,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        record["confirmationDigest"] = hashlib.sha256(
            b"hermes.workflow-marketplace.confirmation.v1\0" + encoded
        ).hexdigest()
    path.write_text(json.dumps(state), encoding="utf-8")
    before = _snapshot(tmp_path)
    metadata = legacy_envelope.stat()
    restarted = MarketplaceTransactionStore(tmp_path)
    for action in (restarted.inspect_recovery, restarted.recover_transactions):
        with pytest.raises(
            WorkflowMarketplaceError, match="paths are inconsistent"
        ) as error:
            action()
        assert error.value.code == "transaction_state_invalid"
        assert _snapshot(tmp_path) == before
        after = legacy_envelope.stat()
        assert (after.st_dev, after.st_ino, after.st_mode) == (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
        )


@pytest.mark.parametrize("workspace", ["staging", "quarantine"])
def test_restart_refuses_foreign_marker_in_isolated_workspace(tmp_path, workspace):
    store = MarketplaceTransactionStore(tmp_path)
    first = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    _install(store, first)
    second = _candidate(tmp_path, _package(tmp_path, "2.0.0"))

    def crash(point):
        if point == "after_backup_move":
            raise SimulatedCrash(point)

    with pytest.raises(SimulatedCrash):
        store.atomic_install(_consume(store, second), review_digest=REVIEW, fault=crash)
    journal = store.list_journals()[0]
    package = (
        journal.staging_path if workspace == "staging" else journal.quarantine_path
    )
    assert package.is_relative_to(tmp_path / "marketplace" / "workflows")
    marker = package.parent / "owner.json"
    value = json.loads(marker.read_bytes())
    value["owner"] = "foreign-authority"
    marker.write_text(json.dumps(value), encoding="utf-8")
    before = _snapshot(tmp_path)
    restarted = MarketplaceTransactionStore(tmp_path)
    assert (
        restarted.inspect_recovery().entries[0].classification == "ownership_mismatch"
    )
    assert restarted.recover_transactions() == ()
    assert restarted.list_journals() == (journal,)
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("consumed", [False, True])
@pytest.mark.parametrize("expired", [False, True])
def test_recovery_inspection_distinguishes_review_from_writer_without_writes(
    tmp_path, consumed, expired
):
    now = [datetime.now(timezone.utc)]
    store = MarketplaceTransactionStore(tmp_path, clock=lambda: now[0])
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    prepared = store.prepare(
        candidate, review_digest=REVIEW, actor="alice", profile="p1"
    )
    if consumed:
        store.consume(prepared.token, actor="alice", profile="p1")
    if expired:
        now[0] += timedelta(hours=1)
    before = _snapshot(tmp_path)
    inspection = store.inspect_recovery()
    assert inspection.complete
    assert inspection.active_writer is (consumed and not expired)
    assert _snapshot(tmp_path) == before
    if consumed or expired:
        assert {e.identity for e in inspection.entries} == {candidate.identity}
        assert {e.transaction_id for e in inspection.entries} == {
            prepared.transaction_id
        }
    else:
        assert inspection.entries == ()
    if consumed:
        assert inspection.entries[0].classification == (
            "owned" if expired else "active_writer"
        )
    # The inspection contains no review secret or private filesystem targets.
    from dataclasses import asdict

    output = json.dumps(asdict(inspection), default=lambda value: value.model_dump())
    assert prepared.token not in output
    assert str(tmp_path) not in output


@pytest.mark.parametrize("damage", ["foreign", "malformed", "symlink"])
def test_recovery_inspection_retains_ambiguous_journals_and_ownership_bytes(
    tmp_path, damage
):
    now = [datetime.now(timezone.utc)]
    store = MarketplaceTransactionStore(tmp_path, clock=lambda: now[0])
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    consumed = _consume(store, candidate)
    marker = consumed.staging_path.parent / "owner.json"
    if damage == "foreign":
        value = json.loads(marker.read_bytes())
        value["packageDigest"] = "0" * 64
        marker.write_text(json.dumps(value))
    elif damage == "malformed":
        marker.write_text("{")
    else:
        target = tmp_path / "unrelated-marker.json"
        marker.replace(target)
        marker.symlink_to(target)
    now[0] += timedelta(hours=1)
    before = _snapshot(tmp_path)
    inspection = store.inspect_recovery()
    assert not inspection.active_writer
    assert inspection.entries[0].classification == "ownership_mismatch"
    assert store.recover_transactions() == ()
    assert store.inspect_recovery() == inspection
    assert _snapshot(tmp_path) == before


def test_recovery_inspection_and_cleanup_share_abandoned_ownership(tmp_path):
    now = [datetime.now(timezone.utc)]
    store = MarketplaceTransactionStore(tmp_path, clock=lambda: now[0])
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    prepared = store.prepare(
        candidate, review_digest=REVIEW, actor="alice", profile="p1"
    )
    # Simulate lost preparation publication after owned staging was created.
    with store._locked() as parent_identity:
        store._write_prepared([], parent_identity=parent_identity)
    unrelated = store.staging_root / ("f" * 32)
    unrelated.mkdir()
    foreign_marker = json.loads(
        (prepared.staging_path.parent / "owner.json").read_bytes()
    )
    foreign_marker["owner"] = "foreign-marketplace"
    foreign_marker["transactionId"] = "f" * 32
    foreign_bytes = json.dumps(foreign_marker)
    (unrelated / "owner.json").write_text(foreign_bytes)
    before = _snapshot(tmp_path)
    inspection = store.inspect_recovery()
    assert [(e.transaction_id, e.identity, e.kind) for e in inspection.entries] == [
        (prepared.transaction_id, candidate.identity, "abandoned_staging")
    ]
    assert _snapshot(tmp_path) == before
    assert store.recover_transactions() == ()
    assert not prepared.staging_path.parent.exists()
    assert (unrelated / "owner.json").read_text() == foreign_bytes
    assert store.inspect_recovery().entries == ()


@pytest.mark.parametrize(
    "damage",
    [
        "malformed",
        "missing",
        "symlink",
        "oversized",
        "wrong_transaction",
        "wrong_destination",
        "unsupported_version",
        "duplicate_owner",
        "foreign_empty",
        "foreign_nonstring",
        "foreign_oversized",
        "foreign_invalid_version",
        "envelope_symlink",
        "envelope_file",
    ],
)
def test_strict_recovery_inspection_cannot_clear_unknown_abandoned_ownership(
    tmp_path, damage
):
    """Catches strict scans reusing best-effort cleanup's invalid-marker skip."""
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    prepared = store.prepare(
        candidate, review_digest=REVIEW, actor="alice", profile="p1"
    )
    with store._locked() as parent_identity:
        store._write_prepared([], parent_identity=parent_identity)
    envelope = prepared.staging_path.parent
    marker = envelope / "owner.json"
    if damage == "malformed":
        marker.write_text("{")
    elif damage == "missing":
        marker.rename(tmp_path / "lost-owner.json")
    elif damage == "symlink":
        target = tmp_path / "foreign-owner.json"
        marker.rename(target)
        marker.symlink_to(target)
    elif damage == "oversized":
        marker.write_text(" " * (32 * 1024 + 1))
    elif damage in {"envelope_symlink", "envelope_file"}:
        target = tmp_path / "external-envelope"
        envelope.rename(target)
        if damage == "envelope_symlink":
            envelope.symlink_to(target, target_is_directory=True)
        else:
            envelope.write_text("unknown transaction entry")
    elif damage == "duplicate_owner":
        marker.write_text('{"owner":"foreign-owner",' + marker.read_text()[1:])
    else:
        value = json.loads(marker.read_bytes())
        if damage == "wrong_transaction":
            value["transactionId"] = "0" * 32
        elif damage == "wrong_destination":
            value["destination"] = str(tmp_path / "unrelated-destination")
        elif damage.startswith("foreign_"):
            value["owner"] = {
                "foreign_empty": "",
                "foreign_nonstring": 7,
                "foreign_oversized": "f" * 129,
                "foreign_invalid_version": "foreign-owner",
            }[damage]
            if damage == "foreign_invalid_version":
                value["schemaVersion"] = 100
        else:
            value["schemaVersion"] = 100
        marker.write_text(json.dumps(value))
    before = _snapshot(tmp_path)
    with pytest.raises(WorkflowMarketplaceError) as error:
        store.inspect_recovery()
    assert error.value.code == "transaction_recovery_inspection_incomplete"
    assert _snapshot(tmp_path) == before
    # The same uncertainty must not widen the legacy deletion authority.
    assert store.recover_transactions() == ()
    assert envelope.exists()
    assert _snapshot(tmp_path) == before
    if damage == "symlink":
        assert marker.is_symlink()


def test_strict_inspection_preserves_unrelated_names_without_parsing_them(tmp_path):
    store = MarketplaceTransactionStore(tmp_path)
    store._ensure_workflow_roots()
    with store._locked():
        pass
    unrelated = store.staging_root / "unrelated"
    unrelated.mkdir()
    (unrelated / "owner.json").write_text("{")
    before = _snapshot(tmp_path)
    assert store.inspect_recovery().complete
    assert store.inspect_recovery().entries == ()
    assert store.recover_transactions() == ()
    assert _snapshot(tmp_path) == before


def test_recovery_inspection_refuses_incomplete_staging_scan_without_writes(tmp_path):
    store = MarketplaceTransactionStore(tmp_path)
    store._ensure_workflow_roots()
    with store._locked():
        pass
    for index in range(321):
        (store.staging_root / f"unrelated-{index}").mkdir()
    before = _snapshot(tmp_path)
    with pytest.raises(WorkflowMarketplaceError) as error:
        store.inspect_recovery()
    assert error.value.code == "transaction_recovery_inspection_incomplete"
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("linked_root", ["staging", "workflows", "quarantine"])
def test_recovery_inspection_does_not_follow_staging_root_symlink(
    tmp_path, linked_root
):
    store = MarketplaceTransactionStore(tmp_path / "profile")
    store.home.mkdir()
    store._ensure_workflow_roots()
    with store._locked():
        pass
    foreign = tmp_path / "foreign"
    root = {
        "staging": store.staging_root,
        "workflows": store.home / "workflows",
        "quarantine": store.quarantine_root,
    }[linked_root]
    root.rename(foreign)
    root.symlink_to(foreign, target_is_directory=True)
    before = _snapshot(tmp_path)
    with pytest.raises(WorkflowMarketplaceError) as error:
        store.inspect_recovery()
    assert error.value.code == "transaction_recovery_inspection_incomplete"
    assert _snapshot(tmp_path) == before


def test_confirmation_token_is_random_hashed_single_use_and_fully_bound(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    store = MarketplaceTransactionStore(tmp_path)

    prepared = store.prepare(
        candidate,
        review_digest=REVIEW,
        actor="alice",
        profile="p1",
    )

    persisted = store.path.read_text(encoding="utf-8")
    assert prepared.token not in persisted
    assert hashlib.sha256(prepared.token.encode()).hexdigest() in persisted
    assert str(candidate.distribution.root) not in persisted
    assert prepared.staging_path.is_relative_to(store.staging_root)
    assert (
        load_distribution(prepared.staging_path).digest == candidate.distribution.digest
    )

    consumed = store.consume(prepared.token, actor="alice", profile="p1")
    assert consumed.consumed is True
    assert consumed.identity == candidate.identity
    assert consumed.review_digest == REVIEW
    assert consumed.actor == "alice"
    assert consumed.profile == "p1"
    assert consumed.destination == candidate.destination.resolve()
    with pytest.raises(WorkflowMarketplaceError) as replay:
        store.consume(prepared.token, actor="alice", profile="p1")
    assert replay.value.code == "confirmation_token_invalid"


def test_confirmation_token_rejects_cross_actor_and_profile_without_consuming(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    store = MarketplaceTransactionStore(tmp_path)
    prepared = store.prepare(
        candidate,
        review_digest=REVIEW,
        actor="alice",
        profile="p1",
    )

    for actor, profile in (("mallory", "p1"), ("alice", "p2")):
        with pytest.raises(WorkflowMarketplaceError) as error:
            store.consume(prepared.token, actor=actor, profile=profile)
        assert error.value.code == "confirmation_token_invalid"

    assert store.consume(prepared.token, actor="alice", profile="p1").consumed


def test_confirmation_token_rejects_expired_and_malformed_values(
    tmp_path: Path,
) -> None:
    now = [datetime(2026, 9, 3, tzinfo=timezone.utc)]
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    store = MarketplaceTransactionStore(tmp_path, clock=lambda: now[0])
    prepared = store.prepare(
        candidate,
        review_digest=REVIEW,
        actor="alice",
        profile="p1",
        ttl_seconds=30,
    )
    now[0] += timedelta(seconds=31)

    for token in (prepared.token, "", "bad token", "x" * 4097, "\N{SNOWMAN}"):
        with pytest.raises(WorkflowMarketplaceError) as error:
            store.consume(token, actor="alice", profile="p1")
        assert error.value.code == "confirmation_token_invalid"


def test_tampered_candidate_identity_cannot_be_authorized_by_token_lookup(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    store = MarketplaceTransactionStore(tmp_path)
    prepared = store.prepare(
        candidate,
        review_digest=REVIEW,
        actor="alice",
        profile="p1",
    )
    state = json.loads(store.path.read_bytes())
    state["transactions"][0]["sourceName"] = "other"
    store.path.write_text(json.dumps(state), encoding="utf-8")
    tampered = store.path.read_bytes()

    with pytest.raises(WorkflowMarketplaceError) as error:
        store.consume(prepared.token, actor="alice", profile="p1")

    assert error.value.code == "transaction_state_invalid"
    assert store.path.read_bytes() == tampered


def test_candidate_credentials_fail_before_transaction_state_is_persisted(
    tmp_path: Path,
) -> None:
    candidate = replace(
        _candidate(tmp_path, _package(tmp_path, "1.0.0")),
        repository_url="https://alice:secret@github.com/example/workflows.git",
    )
    store = MarketplaceTransactionStore(tmp_path)

    with pytest.raises(WorkflowMarketplaceError) as error:
        store.prepare(
            candidate,
            review_digest=REVIEW,
            actor="alice",
            profile="p1",
        )

    assert error.value.code == "transaction_candidate_invalid"
    assert not store.path.exists()


def test_prepare_never_claims_or_removes_an_existing_staging_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugins.workflow.marketplace.transactions as transaction_module

    transaction_id = "a" * 32
    store = MarketplaceTransactionStore(tmp_path)
    store._ensure_workflow_roots()
    unrelated = store.staging_root / transaction_id / "unrelated"
    unrelated.mkdir(parents=True)
    keep = unrelated / "keep"
    keep.write_text("keep", encoding="utf-8")
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    (unrelated.parent / "owner.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "owner": "hermes-workflow-marketplace",
            "transactionId": transaction_id,
            "kind": "staging",
            "identity": candidate.identity.model_dump(mode="json", by_alias=True),
            "destination": str(candidate.destination),
            "packageDigest": candidate.distribution.digest,
            "createdAt": "2026-09-03T12:00:00Z",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        transaction_module.secrets, "token_hex", lambda _size: transaction_id
    )

    with pytest.raises(WorkflowMarketplaceError) as error:
        store.prepare(
            candidate,
            review_digest=REVIEW,
            actor="alice",
            profile="p1",
        )

    assert error.value.code == "transaction_path_occupied"
    assert keep.read_text(encoding="utf-8") == "keep"


def test_changed_staged_candidate_or_review_digest_never_installs(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    store = MarketplaceTransactionStore(tmp_path)
    consumed = _consume(store, candidate)
    (consumed.staging_path / "fixtures" / "sample.json").write_bytes(b"changed")

    with pytest.raises(WorkflowMarketplaceError) as changed:
        store.atomic_install(consumed, review_digest=REVIEW)
    assert changed.value.code == "transaction_candidate_changed"
    assert not candidate.destination.exists()

    second = _consume(store, candidate)
    with pytest.raises(WorkflowMarketplaceError) as review:
        store.atomic_install(second, review_digest="d" * 64)
    assert review.value.code == "transaction_review_changed"
    assert not candidate.destination.exists()


def test_atomic_mutations_require_a_fresh_review_digest(tmp_path: Path) -> None:
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    install = _consume(store, candidate)

    with pytest.raises(WorkflowMarketplaceError) as install_error:
        store.atomic_install(install, review_digest=cast(str, None))
    assert install_error.value.code == "transaction_review_invalid"
    assert not candidate.destination.exists()

    installed = store.atomic_install(install, review_digest=REVIEW)
    _candidate_value, remove = _authorize_remove(store, installed)

    with pytest.raises(WorkflowMarketplaceError) as remove_error:
        store.atomic_remove(remove, review_digest=cast(str, None))
    assert remove_error.value.code == "transaction_review_invalid"
    assert candidate.destination.exists()
    assert store.installed_store.get(candidate.identity) == installed


def test_stale_prepared_update_cannot_replace_newer_destination_state(
    tmp_path: Path,
) -> None:
    store = MarketplaceTransactionStore(tmp_path)
    first = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    second = _candidate(tmp_path, _package(tmp_path, "2.0.0"))
    third = _candidate(tmp_path, _package(tmp_path, "3.0.0"))
    _install(store, first)
    second_review = "2" * 64
    third_review = "3" * 64
    second_prepared = store.prepare(
        second,
        review_digest=second_review,
        actor="alice",
        profile="p1",
    )
    third_prepared = store.prepare(
        third,
        review_digest=third_review,
        actor="alice",
        profile="p1",
    )
    stale = store.consume(second_prepared.token, actor="alice", profile="p1")
    current = store.consume(third_prepared.token, actor="alice", profile="p1")
    installed = store.atomic_install(current, review_digest=third_review)
    installed_bytes = _snapshot(third.destination)

    with pytest.raises(WorkflowMarketplaceError) as error:
        store.atomic_install(stale, review_digest=second_review)

    assert error.value.code == "installed_package_changed"
    assert _snapshot(third.destination) == installed_bytes
    assert store.installed_store.get(third.identity) == installed


def test_atomic_mutations_reject_direct_or_unconsumed_authorization(
    tmp_path: Path,
) -> None:
    store = MarketplaceTransactionStore(tmp_path)
    install_candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    unconsumed_install = store.prepare(
        install_candidate,
        review_digest=REVIEW,
        actor="alice",
        profile="p1",
    )

    for unauthorized in (install_candidate, unconsumed_install):
        with pytest.raises(WorkflowMarketplaceError) as error:
            store.atomic_install(
                cast(PreparedTransaction, unauthorized), review_digest=REVIEW
            )
        assert error.value.code == "confirmation_token_invalid"

    consumed_install = store.consume(
        unconsumed_install.token,
        actor="alice",
        profile="p1",
    )
    with pytest.raises(WorkflowMarketplaceError) as wrong_operation:
        store.atomic_remove(consumed_install, review_digest=REVIEW)
    assert wrong_operation.value.code == "confirmation_token_invalid"
    installed = store.atomic_install(consumed_install, review_digest=REVIEW)
    remove_candidate = _removal_candidate(store, installed)
    unconsumed_remove = store.prepare(
        remove_candidate,
        review_digest=REVIEW,
        actor="alice",
        profile="p1",
    )

    for unauthorized in (installed.identity, remove_candidate, unconsumed_remove):
        with pytest.raises(WorkflowMarketplaceError) as error:
            store.atomic_remove(
                cast(PreparedTransaction, unauthorized), review_digest=REVIEW
            )
        assert error.value.code == "confirmation_token_invalid"

    consumed_remove = store.consume(
        unconsumed_remove.token,
        actor="alice",
        profile="p1",
    )
    with pytest.raises(WorkflowMarketplaceError) as wrong_operation:
        store.atomic_install(consumed_remove, review_digest=REVIEW)
    assert wrong_operation.value.code == "confirmation_token_invalid"
    assert store.installed_store.get(installed.identity) == installed


def test_atomic_install_places_verified_copy_and_exact_provenance(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    store = MarketplaceTransactionStore(tmp_path)

    installed = _install(store, candidate)

    assert installed.package_version == "1.0.0"
    assert installed.distribution_digest == candidate.distribution.digest
    assert (
        load_distribution(candidate.destination).digest == candidate.distribution.digest
    )
    assert InstalledPackageStore(tmp_path).get(candidate.identity) == installed
    assert list(store.staging_root.iterdir()) == []
    assert list(store.quarantine_root.iterdir()) == []
    assert store.list_journals() == ()


def test_atomic_install_cancellation_wins_at_exact_pre_mutation_boundary(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    store = MarketplaceTransactionStore(tmp_path)
    consumed = _consume(store, candidate)
    entered = []

    with pytest.raises(WorkflowMarketplaceError) as error:
        store.atomic_install(
            consumed,
            review_digest=REVIEW,
            cancelled=lambda: False,
            enter_atomic=lambda: (entered.append(True), False)[1],
        )

    assert error.value.code == "marketplace_operation_cancelled"
    assert entered == [True]
    assert not candidate.destination.exists()
    with pytest.raises(WorkflowMarketplaceError):
        store.installed_store.get(candidate.identity)
    assert store.list_journals() == ()


def test_atomic_install_masks_cancellation_after_boundary_and_commits(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    store = MarketplaceTransactionStore(tmp_path)
    consumed = _consume(store, candidate)
    cancellation = [False]

    def enter_atomic() -> bool:
        cancellation[0] = True
        return True

    installed = store.atomic_install(
        consumed,
        review_digest=REVIEW,
        cancelled=lambda: cancellation[0],
        enter_atomic=enter_atomic,
    )

    assert installed.identity == candidate.identity
    assert candidate.destination.exists()
    assert store.installed_store.get(candidate.identity) == installed


def test_token_metadata_is_read_only_actor_profile_expiry_bound_and_canonical(
    tmp_path: Path,
) -> None:
    now = [datetime(2026, 9, 4, tzinfo=timezone.utc)]
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    store = MarketplaceTransactionStore(tmp_path, clock=lambda: now[0])
    prepared = store.prepare(
        candidate,
        review_digest=REVIEW,
        actor="alice",
        profile="support",
    )

    metadata = store.inspect_token(prepared.token, actor="alice", profile="support")
    assert metadata.operation == "install"
    assert metadata.identity == candidate.identity
    assert not hasattr(metadata, "token")
    assert (
        store.inspect_token(prepared.token, actor="alice", profile="support")
        == metadata
    )

    for actor, profile in (("mallory", "support"), ("alice", "other")):
        with pytest.raises(WorkflowMarketplaceError) as denied:
            store.inspect_token(prepared.token, actor=actor, profile=profile)
        assert denied.value.code == "confirmation_token_invalid"

    now[0] += timedelta(minutes=6)
    with pytest.raises(WorkflowMarketplaceError) as expired:
        store.inspect_token(prepared.token, actor="alice", profile="support")
    assert expired.value.code == "confirmation_token_invalid"


def test_atomic_install_rejects_noncanonical_or_mismatched_trust_origin(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    store = MarketplaceTransactionStore(tmp_path)
    consumed = _consume(store, candidate)

    for origin in ("marketplace:other/laptop-support", "unsafe origin"):
        with pytest.raises(WorkflowMarketplaceError) as error:
            store.atomic_install(
                consumed,
                review_digest=REVIEW,
                trust_origin=origin,
            )
        assert error.value.code == "transaction_trust_origin_invalid"

    assert not candidate.destination.exists()


def test_old_consumed_journal_without_trust_origin_retains_legacy_behavior(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    store = MarketplaceTransactionStore(tmp_path)
    consumed = _consume(store, candidate)
    state = json.loads(store.journal_path.read_bytes())
    state["journals"][0].pop("trustOrigin", None)
    store.journal_path.write_text(json.dumps(state), encoding="utf-8")

    installed = store.atomic_install(consumed, review_digest=REVIEW)

    assert installed.package_version == "1.0.0"
    assert store.list_journals() == ()


def test_install_recovery_replays_identity_bound_pending_trust_revocation(
    tmp_path: Path,
) -> None:
    store = MarketplaceTransactionStore(tmp_path)
    first = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    second = _candidate(tmp_path, _package(tmp_path, "2.0.0"))
    _install(store, first)
    origin = "marketplace:company/laptop-support"
    digest = "1" * 64
    risk = "a" * 64
    store.trust_store.trust_origin(
        digest, actor="marketplace", risk_digest=risk, origin=origin
    )
    store.trust_store.trust(digest, actor="manual", risk_digest=risk)

    def crash(point: str) -> None:
        if point == "after_trust_revoke_pending":
            raise SimulatedCrash(point)

    with pytest.raises(SimulatedCrash):
        store.atomic_install(
            _consume(store, second),
            review_digest=REVIEW,
            trust_origin=origin,
            fault=crash,
        )
    assert (
        origin
        in store.trust_store.snapshot_read_only(max_bytes=1024 * 1024)["records"][
            digest
        ]["grants"]
    )

    store.recover_transactions()
    store.recover_transactions()

    grants = store.trust_store.snapshot_read_only(max_bytes=1024 * 1024)["records"][
        digest
    ]["grants"]
    assert set(grants) == {"manual"}
    assert store.installed_store.get(first.identity).package_version == "2.0.0"
    assert store.list_journals() == ()


def test_post_trust_commit_failure_never_rolls_back_package_or_provenance(
    tmp_path: Path,
) -> None:
    store = MarketplaceTransactionStore(tmp_path)
    first = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    second = _candidate(tmp_path, _package(tmp_path, "2.0.0"))
    _install(store, first)
    origin = "marketplace:company/laptop-support"
    digest = "1" * 64
    risk = "a" * 64
    store.trust_store.trust_origin(
        digest, actor="marketplace", risk_digest=risk, origin=origin
    )
    store.trust_store.trust(digest, actor="manual", risk_digest=risk)

    def fail_after_trust_commit(point: str) -> None:
        if point == "after_trust_revoke":
            raise OSError("journal replacement unavailable")

    with pytest.raises(WorkflowMarketplaceError) as error:
        store.atomic_install(
            _consume(store, second),
            review_digest=REVIEW,
            trust_origin=origin,
            fault=fail_after_trust_commit,
        )
    assert error.value.code == "transaction_rollback_failed"
    assert store.installed_store.get(first.identity).package_version == "2.0.0"
    grants = store.trust_store.snapshot_read_only(max_bytes=1024 * 1024)["records"][
        digest
    ]["grants"]
    assert set(grants) == {"manual"}
    assert store.list_journals()

    store.recover_transactions()

    assert store.installed_store.get(first.identity).package_version == "2.0.0"
    assert store.list_journals() == ()


def test_remove_recovery_replays_identity_bound_pending_trust_revocation(
    tmp_path: Path,
) -> None:
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    installed = _install(store, candidate)
    origin = "marketplace:company/laptop-support"
    digest = "1" * 64
    risk = "a" * 64
    store.trust_store.trust_origin(
        digest, actor="marketplace", risk_digest=risk, origin=origin
    )
    _candidate_value, removal = _authorize_remove(store, installed)

    def crash(point: str) -> None:
        if point == "after_trust_revoke_pending":
            raise SimulatedCrash(point)

    with pytest.raises(SimulatedCrash):
        store.atomic_remove(
            removal,
            review_digest=REVIEW,
            trust_origin=origin,
            fault=crash,
        )

    store.recover_transactions()

    assert not candidate.destination.exists()
    assert store.trust_store.check_read_only(digest, risk_digest=risk) == "untrusted"
    assert store.list_journals() == ()


@pytest.mark.parametrize(
    "tampered_origin",
    ["marketplace:other/laptop-support", "unsafe origin"],
)
def test_recovery_rejects_mismatched_journal_trust_origin_before_revocation(
    tmp_path: Path,
    tampered_origin: str,
) -> None:
    now = [datetime(2026, 9, 3, tzinfo=timezone.utc)]
    store = MarketplaceTransactionStore(tmp_path, clock=lambda: now[0])
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    consumed = _consume(store, candidate)
    state = json.loads(store.journal_path.read_bytes())
    state["journals"][0]["trustOrigin"] = tampered_origin
    store.journal_path.write_text(json.dumps(state), encoding="utf-8")
    now[0] += timedelta(minutes=6)
    store.trust_store.trust_origin(
        "1" * 64,
        actor="marketplace",
        risk_digest="a" * 64,
        origin="marketplace:company/laptop-support",
    )
    before = store.trust_store.snapshot_read_only(max_bytes=1024 * 1024)

    with pytest.raises(WorkflowMarketplaceError) as error:
        store.recover_transactions()

    assert error.value.code == "transaction_state_invalid"
    assert store.trust_store.snapshot_read_only(max_bytes=1024 * 1024) == before
    assert not consumed.destination.exists()


def test_atomic_install_keeps_same_package_id_from_two_sources_separate(
    tmp_path: Path,
) -> None:
    store = MarketplaceTransactionStore(tmp_path)
    company = _candidate(
        tmp_path,
        _package(tmp_path, "1.0.0", suffix="company"),
        source_key="company",
        source_name="company",
    )
    mirror = _candidate(
        tmp_path,
        _package(tmp_path, "1.0.0", suffix="mirror"),
        source_key="mirror",
        source_name="mirror",
    )

    company_installed = _install(store, company)
    mirror_installed = _install(store, mirror)

    assert company.destination != mirror.destination
    assert store.installed_store.get(company.identity) == company_installed
    assert store.installed_store.get(mirror.identity) == mirror_installed


def test_failed_provenance_write_restores_previous_package_and_provenance(
    tmp_path: Path,
) -> None:
    store = MarketplaceTransactionStore(tmp_path)
    first = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    second = _candidate(tmp_path, _package(tmp_path, "2.0.0"))
    old = _install(store, first)
    before = _snapshot(first.destination)

    def fail_write(_provenance):
        raise OSError("simulated provenance failure")

    with pytest.raises(OSError, match="simulated provenance failure"):
        store.atomic_install(
            _consume(store, second),
            review_digest=REVIEW,
            provenance_writer=fail_write,
        )

    assert _snapshot(first.destination) == before
    assert InstalledPackageStore(tmp_path).get(first.identity) == old
    assert store.list_journals() == ()


def test_failed_first_install_restores_absent_package_and_provenance_file(
    tmp_path: Path,
) -> None:
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))

    def fail_write(_provenance):
        raise OSError("simulated provenance failure")

    with pytest.raises(OSError, match="simulated provenance failure"):
        store.atomic_install(
            _consume(store, candidate),
            review_digest=REVIEW,
            provenance_writer=fail_write,
        )

    assert not candidate.destination.exists()
    assert not store.installed_store.path.exists()
    assert store.list_journals() == ()


def test_destination_symlink_fails_without_touching_target(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    store = MarketplaceTransactionStore(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker"
    marker.write_text("keep", encoding="utf-8")
    prepared = store.prepare(
        candidate,
        review_digest=REVIEW,
        actor="alice",
        profile="p1",
    )
    try:
        candidate.destination.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("host does not permit directory symlinks")

    with pytest.raises(WorkflowMarketplaceError) as error:
        store.atomic_install(
            store.consume(prepared.token, actor="alice", profile="p1"),
            review_digest=REVIEW,
        )

    assert error.value.code == "transaction_destination_invalid"
    assert marker.read_text(encoding="utf-8") == "keep"
    assert candidate.destination.is_symlink()


def test_concurrent_marketplace_lock_timeout_is_stable(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    store = MarketplaceTransactionStore(tmp_path, lock_timeout_seconds=0.05)
    store.prepare(
        candidate,
        review_digest=REVIEW,
        actor="alice",
        profile="p1",
    )
    entered = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with workflow_lock(store.lock_path):
            entered.set()
            release.wait(timeout=2)

    thread = threading.Thread(target=hold_lock)
    thread.start()
    assert entered.wait(timeout=1)
    try:
        with pytest.raises(WorkflowMarketplaceError) as error:
            store.prepare(
                candidate,
                review_digest=REVIEW,
                actor="alice",
                profile="p1",
            )
        assert error.value.code == "transaction_lock_timeout"
    finally:
        release.set()
        thread.join(timeout=1)


@pytest.mark.parametrize(
    ("crash_point", "expected_version"),
    [
        ("before_initial_journal", "1.0.0"),
        ("after_initial_journal", "1.0.0"),
        ("after_backup_move", "1.0.0"),
        ("after_candidate_swap", "1.0.0"),
        ("after_provenance_write", "2.0.0"),
        ("after_backup_retired", "2.0.0"),
        ("after_staging_retired", "2.0.0"),
    ],
)
def test_interrupted_install_recovers_idempotently_at_every_visibility_boundary(
    tmp_path: Path,
    crash_point: str,
    expected_version: str,
) -> None:
    now = [datetime(2026, 9, 3, tzinfo=timezone.utc)]
    store = MarketplaceTransactionStore(tmp_path, clock=lambda: now[0])
    first = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    second = _candidate(tmp_path, _package(tmp_path, "2.0.0"))
    _install(store, first)
    unrelated = store.quarantine_root / "unrelated"
    unrelated.mkdir(parents=True)
    (unrelated / "keep").write_text("keep", encoding="utf-8")

    def crash(point: str) -> None:
        if point == crash_point:
            raise SimulatedCrash(point)

    with pytest.raises(SimulatedCrash):
        store.atomic_install(_consume(store, second), review_digest=REVIEW, fault=crash)

    if crash_point == "before_initial_journal":
        now[0] += timedelta(minutes=6)
    store.recover_transactions()
    first_state = _snapshot(first.destination)
    store.recover_transactions()

    installed = InstalledPackageStore(tmp_path).get(first.identity)
    assert installed.package_version == expected_version
    assert load_distribution(first.destination).manifest.version == expected_version
    assert _snapshot(first.destination) == first_state
    assert (unrelated / "keep").read_text(encoding="utf-8") == "keep"
    assert store.list_journals() == ()


def test_rollback_write_failure_remains_journaled_and_recovery_restores_v1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugins.workflow.marketplace.provenance as provenance_module

    store = MarketplaceTransactionStore(tmp_path)
    first = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    second = _candidate(tmp_path, _package(tmp_path, "2.0.0"))
    old = _install(store, first)
    real_write = provenance_module.atomic_write_text
    calls = 0

    def fail_second_write(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("rollback write failed")
        return real_write(*args, **kwargs)

    monkeypatch.setattr(provenance_module, "atomic_write_text", fail_second_write)

    def write_then_fail(provenance):
        InstalledPackageStore(tmp_path).put(provenance)
        raise OSError("writer failed after replacement")

    with pytest.raises(WorkflowMarketplaceError) as error:
        store.atomic_install(
            _consume(store, second),
            review_digest=REVIEW,
            provenance_writer=write_then_fail,
        )
    assert error.value.code == "transaction_rollback_failed"
    assert store.list_journals()

    monkeypatch.setattr(provenance_module, "atomic_write_text", real_write)
    store.recover_transactions()

    assert InstalledPackageStore(tmp_path).get(first.identity) == old
    assert load_distribution(first.destination).manifest.version == "1.0.0"
    assert store.list_journals() == ()


def test_atomic_remove_has_matching_rollback_and_success_semantics(
    tmp_path: Path,
) -> None:
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    installed = _install(store, candidate)
    before = _snapshot(candidate.destination)

    def fail_remove(_identity):
        raise OSError("simulated remove failure")

    _remove_candidate_value, removal = _authorize_remove(store, installed)
    with pytest.raises(OSError, match="simulated remove failure"):
        store.atomic_remove(
            removal,
            review_digest=REVIEW,
            provenance_remover=fail_remove,
        )

    assert _snapshot(candidate.destination) == before
    assert InstalledPackageStore(tmp_path).get(candidate.identity) == installed

    _remove_candidate_value, removal = _authorize_remove(store, installed)
    removed = store.atomic_remove(removal, review_digest=REVIEW)
    assert removed == installed
    assert not candidate.destination.exists()
    with pytest.raises(WorkflowMarketplaceError) as error:
        InstalledPackageStore(tmp_path).get(candidate.identity)
    assert error.value.code == "installed_package_not_found"


def test_atomic_remove_cancellation_before_boundary_preserves_package(
    tmp_path: Path,
) -> None:
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    installed = _install(store, candidate)
    _remove_candidate_value, removal = _authorize_remove(store, installed)

    with pytest.raises(WorkflowMarketplaceError) as error:
        store.atomic_remove(
            removal,
            review_digest=REVIEW,
            cancelled=lambda: True,
            enter_atomic=lambda: pytest.fail("cancel must win before atomic entry"),
        )

    assert error.value.code == "marketplace_operation_cancelled"
    assert candidate.destination.exists()
    assert store.installed_store.get(candidate.identity) == installed


def test_atomic_remove_revalidates_installed_bytes_after_confirmation(
    tmp_path: Path,
) -> None:
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    installed = _install(store, candidate)
    _remove_candidate_value, removal = _authorize_remove(store, installed)
    (candidate.destination / "fixtures" / "sample.json").write_bytes(b"tampered")

    with pytest.raises(WorkflowMarketplaceError) as error:
        store.atomic_remove(removal, review_digest=REVIEW)

    assert error.value.code == "transaction_candidate_changed"
    assert candidate.destination.exists()
    assert InstalledPackageStore(tmp_path).get(candidate.identity) == installed


@pytest.mark.parametrize(
    ("crash_point", "removed"),
    [
        ("after_initial_journal", False),
        ("after_backup_move", False),
        ("after_provenance_remove", True),
        ("after_backup_retired", True),
    ],
)
def test_interrupted_remove_recovers_idempotently(
    tmp_path: Path,
    crash_point: str,
    removed: bool,
) -> None:
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    installed = _install(store, candidate)
    _remove_candidate_value, removal = _authorize_remove(store, installed)

    def crash(point: str) -> None:
        if point == crash_point:
            raise SimulatedCrash(point)

    with pytest.raises(SimulatedCrash):
        store.atomic_remove(
            removal,
            review_digest=REVIEW,
            fault=crash,
        )

    store.recover_transactions()
    store.recover_transactions()

    if removed:
        assert not candidate.destination.exists()
        with pytest.raises(WorkflowMarketplaceError):
            InstalledPackageStore(tmp_path).get(candidate.identity)
    else:
        assert candidate.destination.exists()
        assert InstalledPackageStore(tmp_path).get(candidate.identity) == installed
    assert store.list_journals() == ()


def test_recovery_preserves_live_consumed_authorization_and_unrelated_staging(
    tmp_path: Path,
) -> None:
    now = [datetime(2026, 9, 3, tzinfo=timezone.utc)]
    store = MarketplaceTransactionStore(tmp_path, clock=lambda: now[0])
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    prepared = store.prepare(
        candidate,
        review_digest=REVIEW,
        actor="alice",
        profile="p1",
    )
    consumed = store.consume(prepared.token, actor="alice", profile="p1")
    unrelated_directory = store.staging_root / "unrelated"
    unrelated_directory.mkdir(parents=True)
    (unrelated_directory / "keep").write_text("keep", encoding="utf-8")
    unrelated_file = store.staging_root / "also-keep"
    unrelated_file.write_text("keep", encoding="utf-8")

    store.recover_transactions()

    assert prepared.staging_path.parent.exists()
    assert len(store.list_journals()) == 1
    assert (unrelated_directory / "keep").read_text(encoding="utf-8") == "keep"
    assert unrelated_file.read_text(encoding="utf-8") == "keep"

    installed = store.atomic_install(consumed, review_digest=REVIEW)
    assert store.installed_store.get(candidate.identity) == installed


def test_recovery_expires_abandoned_consumed_authorization_deterministically(
    tmp_path: Path,
) -> None:
    now = [datetime(2026, 9, 3, tzinfo=timezone.utc)]
    store = MarketplaceTransactionStore(tmp_path, clock=lambda: now[0])
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    prepared = store.prepare(
        candidate,
        review_digest=REVIEW,
        actor="alice",
        profile="p1",
    )
    consumed = store.consume(prepared.token, actor="alice", profile="p1")
    now[0] += timedelta(minutes=6)

    assert store.recover_transactions() == (prepared.transaction_id,)
    assert store.recover_transactions() == ()

    assert not prepared.staging_path.parent.exists()
    assert store.list_journals() == ()
    with pytest.raises(WorkflowMarketplaceError) as error:
        store.atomic_install(consumed, review_digest=REVIEW)
    assert error.value.code == "confirmation_token_invalid"


@pytest.mark.parametrize(
    ("operation", "nested_key"),
    [
        ("install", "candidateProvenance"),
        ("remove", "previousProvenance"),
    ],
)
def test_recovery_rejects_nested_journal_provenance_identity_mismatch(
    tmp_path: Path,
    operation: Literal["install", "remove"],
    nested_key: str,
) -> None:
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    installed = None
    if operation == "install":
        prepared = _consume(store, candidate)
    else:
        installed = _install(store, candidate)
        _candidate_value, prepared = _authorize_remove(store, installed)
    state = json.loads(store.journal_path.read_bytes())
    state["journals"][0][nested_key]["identity"]["sourceKey"] = "other"
    store.journal_path.write_text(json.dumps(state), encoding="utf-8")
    package_before = _snapshot(candidate.destination)
    provenance_before = store.installed_store.list_installed()
    journal_before = store.journal_path.read_bytes()

    with pytest.raises(WorkflowMarketplaceError) as error:
        store.recover_transactions()

    assert error.value.code == "transaction_state_invalid"
    assert _snapshot(candidate.destination) == package_before
    assert store.installed_store.list_installed() == provenance_before
    assert store.journal_path.read_bytes() == journal_before
    if installed is None:
        assert prepared.staging_path.parent.exists()


def test_recovery_expires_unconsumed_preparation_and_cleans_its_owned_staging(
    tmp_path: Path,
) -> None:
    now = [datetime(2026, 9, 3, tzinfo=timezone.utc)]
    store = MarketplaceTransactionStore(tmp_path, clock=lambda: now[0])
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    prepared = store.prepare(
        candidate,
        review_digest=REVIEW,
        actor="alice",
        profile="p1",
        ttl_seconds=1,
    )
    now[0] += timedelta(seconds=2)

    store.recover_transactions()

    assert not prepared.staging_path.parent.exists()
    with pytest.raises(WorkflowMarketplaceError) as error:
        store.consume(prepared.token, actor="alice", profile="p1")
    assert error.value.code == "confirmation_token_invalid"


def test_recovery_preserves_owned_marker_mismatch_and_ambiguous_swap(
    tmp_path: Path,
) -> None:
    store = MarketplaceTransactionStore(tmp_path)
    first = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    second = _candidate(tmp_path, _package(tmp_path, "2.0.0"))
    _install(store, first)

    def crash(point: str) -> None:
        if point == "after_backup_move":
            raise SimulatedCrash(point)

    with pytest.raises(SimulatedCrash):
        store.atomic_install(_consume(store, second), review_digest=REVIEW, fault=crash)
    journal = store.list_journals()[0]
    marker = journal.quarantine_path.parent / "owner.json"
    marker.write_text('{"schemaVersion":1,"owner":"someone-else"}\n')

    store.recover_transactions()

    assert marker.exists()
    assert journal.quarantine_path.exists()
    assert store.list_journals() == (journal,)
