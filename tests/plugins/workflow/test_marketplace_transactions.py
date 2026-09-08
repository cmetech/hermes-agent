"""Two-phase and recoverable workflow marketplace transaction contracts."""

from __future__ import annotations

from dataclasses import replace
from contextlib import contextmanager
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
    from plugins.workflow.marketplace import transactions as transaction_module

    original = transaction_module.workflow_lock
    displaced = tmp_path / "displaced-private-root"
    blocked_by_guard = False

    @contextmanager
    def replace_after_validation(*args, **kwargs):
        nonlocal blocked_by_guard
        with original(*args, **kwargs):
            try:
                store.root.rename(displaced)
            except PermissionError:
                assert os.name == "nt"
                blocked_by_guard = True
            else:
                store.root.mkdir(mode=0o700)
            yield

    monkeypatch.setattr(transaction_module, "workflow_lock", replace_after_validation)
    failure = None
    try:
        result = store.prepare(
            candidate, review_digest=REVIEW, actor="alice", profile="p1"
        )
    except WorkflowMarketplaceError as error:
        failure = error
    if blocked_by_guard:
        assert failure is None
        assert (
            load_distribution(result.staging_path).digest
            == candidate.distribution.digest
        )
    else:
        assert failure is not None and failure.code == "transaction_state_invalid"
        assert not store.path.exists()
    assert not candidate.destination.exists()
    assert not store.installed_store.path.exists()


def _workspace_entry_point(store, candidate, action):
    if action == "prepare":
        return lambda: store.prepare(
            candidate, review_digest=REVIEW, actor="alice", profile="p1"
        )
    if action == "recover":
        return store.recover_transactions
    if action == "atomic_install":
        prepared = _consume(store, candidate)
        return lambda: store.atomic_install(prepared, review_digest=REVIEW)
    installed = _install(store, candidate)
    _, removal = _authorize_remove(store, installed)
    return lambda: store.atomic_remove(removal, review_digest=REVIEW)


@pytest.mark.parametrize(
    "action", ["prepare", "recover", "atomic_install", "atomic_remove"]
)
@pytest.mark.parametrize("replacement", ["symlink", "directory"])
@pytest.mark.parametrize("native", [True, False])
def test_defense_in_depth_scratch_mkdir_boundary_preserves_foreign_authority(
    tmp_path, monkeypatch, action, replacement, native
):
    """Observed dir_fd defense, not a portable same-account syscall guarantee."""
    from plugins.workflow.marketplace import transactions as transaction_module

    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    invoke = _workspace_entry_point(store, candidate, action)
    original_identity = store._shared_store._ensure_private_root()
    original_bytes = _snapshot(store.root)
    destination_before = _snapshot(candidate.destination)
    foreign = tmp_path / "foreign-authority"
    foreign.mkdir(mode=0o700)
    (foreign / "evidence").write_bytes(b"untouched foreign bytes")
    foreign_identity = foreign.stat()
    displaced = tmp_path / "displaced-authority"
    attempted = False
    blocked = False
    observations = []
    original_mkdir = os.mkdir

    def mkdir(path, *args, **kwargs):
        nonlocal attempted, foreign, blocked
        if Path(path).name == ".staging" and not attempted:
            attempted = True
            try:
                store.root.rename(displaced)
            except PermissionError:
                assert os.name == "nt"
                blocked = True
            else:
                if replacement == "symlink":
                    store.root.symlink_to(foreign, target_is_directory=True)
                else:
                    foreign.rename(store.root)
                    foreign = store.root
        try:
            return original_mkdir(path, *args, **kwargs)
        finally:
            observations.append(sorted(item.name for item in foreign.iterdir()))

    if not native:
        monkeypatch.setattr(transaction_module, "_DIRECTORY_FD_SUPPORTED", False)
    monkeypatch.setattr(os, "mkdir", mkdir)
    error = None
    try:
        invoke()
    except WorkflowMarketplaceError as failure:
        error = failure
    assert all(entries == ["evidence"] for entries in observations), observations
    assert _snapshot(foreign) == {"evidence": b"untouched foreign bytes"}
    after = foreign.stat()
    assert (after.st_dev, after.st_ino, after.st_mode) == (
        foreign_identity.st_dev,
        foreign_identity.st_ino,
        foreign_identity.st_mode,
    )
    original = displaced if displaced.exists() else store.root
    assert (original.stat().st_dev, original.stat().st_ino) == original_identity
    if not blocked:
        assert _snapshot(original) == original_bytes
        assert _snapshot(candidate.destination) == destination_before
        assert error is not None and error.code == "transaction_state_invalid"
    if not native and os.name != "nt":
        assert not attempted, "unsupported POSIX must refuse before scratch mkdir"
    else:
        assert attempted


@pytest.mark.parametrize("replacement", ["symlink", "directory"])
@pytest.mark.parametrize(
    "window",
    [
        "marker",
        "envelope_mkdir",
        "owner_open",
        "owner_replace",
        "copy",
        "package_mkdir",
        "file_open",
        "file_write",
        "fsync",
        "state",
    ],
)
@pytest.mark.parametrize("native", [True, False])
def test_preparation_writes_stay_with_original_authority(
    tmp_path, monkeypatch, replacement, window, native
):
    """Phase checks plus extra syscall probes; only phase checks are portable."""
    from plugins.workflow.marketplace import transactions as transaction_module

    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    original_identity = store._shared_store._ensure_private_root()
    foreign = tmp_path / "foreign-authority"
    foreign.mkdir(mode=0o700)
    (foreign / ".staging").mkdir(mode=0o700)
    (foreign / "evidence").write_bytes(b"untouched foreign bytes")
    foreign_identity = foreign.stat()
    displaced = tmp_path / "displaced-authority"
    attempted = False
    blocked = False
    observations = []
    copying = False

    def observe():
        observations.append(
            sorted(str(p.relative_to(foreign)) for p in foreign.rglob("*"))
        )

    def swap():
        nonlocal attempted, blocked, foreign
        if attempted:
            return
        attempted = True
        try:
            store.root.rename(displaced)
        except PermissionError:
            assert os.name == "nt"
            blocked = True
        else:
            if replacement == "symlink":
                store.root.symlink_to(foreign, target_is_directory=True)
            else:
                foreign.rename(store.root)
                foreign = store.root

    original_marker = store._write_marker
    original_copy = store._copy_verified_distribution
    original_state = store._write_prepared

    def marker(*args, **kwargs):
        if window == "marker":
            swap()
        return original_marker(*args, **kwargs)

    def copy(*args, **kwargs):
        nonlocal copying
        copying = True
        if window == "copy":
            swap()
        return original_copy(*args, **kwargs)

    def state(*args, **kwargs):
        if window == "state":
            swap()
        return original_state(*args, **kwargs)

    def instrument(name):
        original = getattr(os, name)

        def wrapped(*args, **kwargs):
            path = (
                Path(os.fsdecode(args[0])).name
                if isinstance(args[0], (str, bytes, os.PathLike))
                else ""
            )
            if (
                (window == "envelope_mkdir" and name == "mkdir" and len(path) == 32)
                or (
                    window == "owner_open"
                    and name == "open"
                    and path.startswith("owner.json.tmp-")
                )
                or (
                    window == "owner_replace"
                    and name == "replace"
                    and path.startswith("owner.json.tmp-")
                )
                or (window == "package_mkdir" and name == "mkdir" and path == "package")
                or (
                    window == "file_open"
                    and name == "open"
                    and copying
                    and args[1] & os.O_CREAT
                )
                or (window == "file_write" and name == "write" and copying)
                or (window == "fsync" and name == "fsync" and copying)
            ):
                swap()
            try:
                return original(*args, **kwargs)
            finally:
                if attempted:
                    observe()

        monkeypatch.setattr(os, name, wrapped)

    monkeypatch.setattr(store, "_write_marker", marker)
    monkeypatch.setattr(store, "_copy_verified_distribution", copy)
    monkeypatch.setattr(store, "_write_prepared", state)
    for name in ("mkdir", "open", "write", "replace", "fsync"):
        instrument(name)
    if not native:
        monkeypatch.setattr(transaction_module, "_DIRECTORY_FD_SUPPORTED", False)
    error = None
    result = None
    try:
        result = store.prepare(
            candidate, review_digest=REVIEW, actor="alice", profile="p1"
        )
    except (WorkflowMarketplaceError, OSError) as failure:
        error = failure
    observe()
    assert all(entries == [".staging", "evidence"] for entries in observations), (
        observations
    )
    assert _snapshot(foreign) == {"evidence": b"untouched foreign bytes"}
    after = foreign.stat()
    assert (after.st_dev, after.st_ino, after.st_mode) == (
        foreign_identity.st_dev,
        foreign_identity.st_ino,
        foreign_identity.st_mode,
    )
    original = displaced if displaced.exists() else store.root
    assert (original.stat().st_dev, original.stat().st_ino) == original_identity
    assert not candidate.destination.exists()
    if blocked:
        assert error is None and result is not None
    else:
        assert isinstance(error, WorkflowMarketplaceError)
        assert error.code.startswith("transaction_state_")
        assert not (original / "transactions.json").exists()
    if not native and os.name != "nt":
        assert not attempted
    else:
        assert attempted, (window, error)


@pytest.mark.parametrize("marker_present", [True, False])
def test_retained_failed_preparation_requires_durable_ownership_for_recovery(
    tmp_path, monkeypatch, marker_present
):
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    original_open = os.open
    original_rmtree = shutil.rmtree

    def open_file(path, flags, *args, **kwargs):
        name = Path(path).name
        if flags & os.O_CREAT and (
            (marker_present and name == "digests.json")
            or (not marker_present and name.startswith("owner.json.tmp-"))
        ):
            raise OSError("injected staging write failure")
        return original_open(path, flags, *args, **kwargs)

    def unavailable_cleanup(*args, **kwargs):
        raise PermissionError("owned directory cannot presently be deleted")

    monkeypatch.setattr(os, "open", open_file)
    monkeypatch.setattr(shutil, "rmtree", unavailable_cleanup)
    with pytest.raises(WorkflowMarketplaceError) as error:
        store.prepare(candidate, review_digest=REVIEW, actor="alice", profile="p1")
    assert error.value.code == (
        "transaction_state_write_failed"
        if marker_present
        else "transaction_recovery_inspection_incomplete"
    )
    monkeypatch.setattr(os, "open", original_open)
    monkeypatch.setattr(shutil, "rmtree", original_rmtree)
    (envelope,) = store.staging_root.iterdir()
    assert (envelope / "owner.json").exists() == marker_present
    assert not store.path.exists()
    assert not candidate.destination.exists()
    before = _snapshot(envelope)
    restarted = MarketplaceTransactionStore(tmp_path)
    if marker_present:
        inspection = restarted.inspect_recovery()
        assert inspection.complete and len(inspection.entries) == 1
        restarted.recover_transactions()
        assert not envelope.exists()
        assert restarted.inspect_recovery().entries == ()
        assert restarted.recover_transactions() == ()
    else:
        with pytest.raises(WorkflowMarketplaceError) as inspection:
            restarted.inspect_recovery()
        assert inspection.value.code == "transaction_recovery_inspection_incomplete"
        restarted.recover_transactions()
        assert envelope.is_dir() and _snapshot(envelope) == before


@pytest.mark.parametrize("failure", ["success", "marker", "copy", "state", "cancel"])
def test_preparation_closes_retained_descriptors_and_cleans_owned_failures(
    tmp_path, monkeypatch, failure
):
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    descriptors = set()
    original_open, original_close = os.open, os.close

    def open_file(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        descriptors.add(descriptor)
        return descriptor

    def close(descriptor):
        try:
            return original_close(descriptor)
        finally:
            descriptors.discard(descriptor)

    def fail(*args, **kwargs):
        if failure == "cancel":
            raise SimulatedCrash("cancelled preparation")
        raise OSError("injected preparation failure")

    monkeypatch.setattr(os, "open", open_file)
    monkeypatch.setattr(os, "close", close)
    if failure != "success":
        monkeypatch.setattr(
            store,
            {
                "marker": "_write_marker",
                "copy": "_copy_verified_distribution",
                "state": "_write_prepared",
                "cancel": "_copy_verified_distribution",
            }[failure],
            fail,
        )
        with pytest.raises(
            SimulatedCrash if failure == "cancel" else WorkflowMarketplaceError
        ):
            store.prepare(candidate, review_digest=REVIEW, actor="alice", profile="p1")
        if os.name != "nt":
            assert list(store.staging_root.iterdir()) == []
        assert not store.path.exists()
    else:
        prepared = store.prepare(
            candidate, review_digest=REVIEW, actor="alice", profile="p1"
        )
        assert (
            load_distribution(prepared.staging_path).digest
            == candidate.distribution.digest
        )
    # fdopen closes at the file-object boundary, outside os.close's Python API.
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize("window", ["envelope_stat", "marker_tamper"])
def test_unprovable_failed_preparation_is_preserved_for_inspection(
    tmp_path, monkeypatch, window
):
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    envelope = None
    original_stat = os.stat

    def stat_path(path, *args, **kwargs):
        if (
            window == "envelope_stat"
            and isinstance(path, (str, os.PathLike))
            and len(Path(path).name) == 32
        ):
            raise OSError("cannot identify newly created envelope")
        return original_stat(path, *args, **kwargs)

    def tamper(distribution, destination, **kwargs):
        nonlocal envelope
        envelope = destination.parent
        (envelope / "owner.json").write_bytes(b"foreign ownership evidence")
        raise OSError("injected copy failure")

    monkeypatch.setattr(os, "stat", stat_path)
    if window == "marker_tamper":
        monkeypatch.setattr(store, "_copy_verified_distribution", tamper)
    with pytest.raises(WorkflowMarketplaceError) as error:
        store.prepare(candidate, review_digest=REVIEW, actor="alice", profile="p1")
    assert error.value.code == "transaction_recovery_inspection_incomplete"
    monkeypatch.setattr(os, "stat", original_stat)
    (envelope,) = store.staging_root.iterdir()
    if window == "marker_tamper":
        assert (envelope / "owner.json").read_bytes() == b"foreign ownership evidence"
    assert not store.path.exists()
    with pytest.raises(WorkflowMarketplaceError) as inspection:
        MarketplaceTransactionStore(tmp_path).inspect_recovery()
    assert inspection.value.code == "transaction_recovery_inspection_incomplete"


def test_preparation_keeps_descriptor_use_bounded_by_copy_depth(tmp_path, monkeypatch):
    store = MarketplaceTransactionStore(tmp_path)
    root = _package(tmp_path, "1.0.0")
    for index in range(270):
        directory = root / "fixtures" / f"member-{index:03d}"
        directory.mkdir()
        (directory / "sample.json").write_bytes(b"{}\n")
    _publish(root)
    candidate = _candidate(tmp_path, root)
    original_open, original_close = os.open, os.close
    retained = set()
    maximum = 0

    def open_file(*args, **kwargs):
        nonlocal maximum
        descriptor = original_open(*args, **kwargs)
        retained.add(descriptor)
        maximum = max(maximum, len(retained))
        return descriptor

    def close(descriptor):
        try:
            return original_close(descriptor)
        finally:
            retained.discard(descriptor)

    monkeypatch.setattr(os, "open", open_file)
    monkeypatch.setattr(os, "close", close)
    prepared = store.prepare(
        candidate, review_digest=REVIEW, actor="alice", profile="p1"
    )
    assert (
        load_distribution(prepared.staging_path).digest == candidate.distribution.digest
    )
    # This shallow package must not accumulate one live descriptor per sibling.
    assert maximum < 32


@pytest.mark.parametrize(
    "damage", ["marker", "package_bytes", "envelope", "remove_bytes", "provenance"]
)
def test_named_phase_before_prepared_publication_rechecks_authority(
    tmp_path, monkeypatch, damage
):
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    if damage == "remove_bytes":
        candidate = _removal_candidate(store, _install(store, candidate))
    state_before = store.path.read_bytes() if store.path.exists() else None
    destination_before = _snapshot(candidate.destination)
    original = store._write_prepared
    evidence = None
    before = None

    def before_publication(*args, **kwargs):
        nonlocal evidence, before
        if damage == "remove_bytes":
            evidence = candidate.destination / "fixtures" / "sample.json"
            evidence.write_bytes(b"changed installed package")
            return original(*args, **kwargs)
        (envelope,) = store.staging_root.iterdir()
        if damage == "marker":
            evidence = envelope / "owner.json"
            evidence.write_bytes(b"foreign ownership")
            before = evidence.read_bytes()
        elif damage == "package_bytes":
            evidence = envelope / "package" / "fixtures" / "sample.json"
            evidence.write_bytes(b"changed candidate")
        elif damage == "provenance":
            evidence = store.installed_store.path
            evidence.write_bytes(b"unproven installed state")
            before = evidence.read_bytes()
        else:
            try:
                envelope.rename(tmp_path / "displaced-envelope")
            except PermissionError:
                assert os.name == "nt"
                return original(*args, **kwargs)
            envelope.mkdir(mode=0o700)
            evidence = envelope / "foreign-evidence"
            evidence.write_bytes(b"foreign directory")
            before = evidence.read_bytes()
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "_write_prepared", before_publication)
    failure = None
    try:
        store.prepare(candidate, review_digest=REVIEW, actor="alice", profile="p1")
    except WorkflowMarketplaceError as error:
        failure = error
    if evidence is None:
        assert os.name == "nt" and damage == "envelope"
        assert failure is None
    else:
        assert failure is not None, "changed preparation must not publish success"
        assert (
            store.path.read_bytes() if store.path.exists() else None
        ) == state_before, "changed preparation must not publish state"
        if before is not None:
            assert evidence.read_bytes() == before
    if damage != "remove_bytes":
        assert _snapshot(candidate.destination) == destination_before


@pytest.mark.parametrize(
    "action,phase,damage",
    [
        (action, phase, damage)
        for action in ("install", "update", "remove")
        for phase in (
            "before_initial_journal",
            "after_initial_journal",
            "after_backup_move",
        )
        for damage in ("marker", "journal", "root", "envelope")
        if not (
            action == "remove"
            and phase == "before_initial_journal"
            and damage in {"marker", "envelope"}
        )
    ]
    + (
        [("install", "after_initial_journal", "workspace_mode")]
        if os.name != "nt"
        else []
    ),
)
def test_named_phase_before_mutation_rechecks_transaction_authority(
    tmp_path, action, phase, damage
):
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    if action == "install":
        prepared = _consume(store, candidate)
    else:
        installed = _install(store, candidate)
        if action == "update":
            candidate = _candidate(tmp_path, _package(tmp_path, "2.0.0"))
            prepared = _consume(store, candidate)
        else:
            _, prepared = _authorize_remove(store, installed)
    observed = None
    evidence = None
    evidence_before = None

    def replace_at_phase(point):
        nonlocal observed, evidence, evidence_before
        if point != phase:
            return
        if damage == "root":
            displaced = tmp_path / "displaced-root"
            store.root.rename(displaced)
            shutil.copytree(displaced, store.root)
            evidence = store.root
            evidence_before = _snapshot(evidence)
        elif damage == "journal":
            evidence = store.journal_path
            state = json.loads(evidence.read_bytes())
            state["journals"][0]["phase"] = (
                f"{prepared.operation}_provenance_write_pending"
            )
            evidence.write_text(json.dumps(state))
            evidence_before = evidence.read_bytes()
        elif damage == "workspace_mode":
            evidence = store.staging_root
            evidence.chmod(0o755)
            evidence_before = _snapshot(evidence)
        elif damage == "envelope":
            root = store.quarantine_root if action == "remove" else store.staging_root
            evidence = root / prepared.transaction_id
            displaced = tmp_path / "displaced-envelope"
            evidence.rename(displaced)
            shutil.copytree(displaced, evidence)
            evidence_before = _snapshot(evidence)
        else:
            root = store.quarantine_root if action == "remove" else store.staging_root
            evidence = root / prepared.transaction_id / "owner.json"
            evidence.write_bytes(b"foreign ownership evidence")
            evidence_before = evidence.read_bytes()
        observed = _snapshot(candidate.destination)

    failure = None
    try:
        mutation = store.atomic_remove if action == "remove" else store.atomic_install
        mutation(prepared, review_digest=REVIEW, fault=replace_at_phase)
    except WorkflowMarketplaceError as error:
        failure = error
    assert observed is not None, "named mutation phase must execute"
    assert failure is not None, "changed authority must not commit"
    assert _snapshot(candidate.destination) == observed, (
        "no installed mutation after detection"
    )
    assert evidence is not None
    assert (
        _snapshot(evidence) if evidence.is_dir() else evidence.read_bytes()
    ) == evidence_before


@pytest.mark.parametrize("action", ["install", "update", "remove"])
@pytest.mark.parametrize("damage", ["marker", "envelope", "journal"])
def test_named_phase_before_cleanup_preserves_unproven_marker(
    tmp_path, monkeypatch, action, damage
):
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    if action == "install":
        prepared = _consume(store, candidate)
    else:
        installed = _install(store, candidate)
        if action == "update":
            candidate = _candidate(tmp_path, _package(tmp_path, "2.0.0"))
            prepared = _consume(store, candidate)
        else:
            _, prepared = _authorize_remove(store, installed)
    original = store._remove_owned_envelope
    evidence = None
    before = None
    installed_at_cleanup = None

    def before_cleanup(envelope, **kwargs):
        nonlocal evidence, before, installed_at_cleanup
        evidence = envelope
        if damage == "envelope":
            displaced = tmp_path / "displaced-cleanup-envelope"
            envelope.rename(displaced)
            shutil.copytree(displaced, envelope)
        elif damage == "journal":
            state = json.loads(store.journal_path.read_bytes())
            state["journals"][0]["phase"] = f"{prepared.operation}_consumed"
            store.journal_path.write_text(json.dumps(state))
        else:
            (envelope / "owner.json").write_bytes(b"unproven cleanup ownership")
        before = _snapshot(envelope)
        installed_at_cleanup = _snapshot(candidate.destination)
        return original(envelope, **kwargs)

    monkeypatch.setattr(store, "_remove_owned_envelope", before_cleanup)
    with pytest.raises(WorkflowMarketplaceError):
        mutation = store.atomic_remove if action == "remove" else store.atomic_install
        mutation(prepared, review_digest=REVIEW)
    assert evidence is not None and _snapshot(evidence) == before
    assert _snapshot(candidate.destination) == installed_at_cleanup
    assert store.list_journals(), "uncertain cleanup remains journaled"


@pytest.mark.parametrize(
    "action", ["prepare", "recover", "atomic_install", "atomic_remove"]
)
@pytest.mark.parametrize("replacement", ["symlink", "directory"])
@pytest.mark.parametrize("window", ["first_validation", "after_children"])
@pytest.mark.parametrize("descriptor_support", [True, False])
def test_private_authority_replacement_never_writes_foreign_workspace(
    tmp_path, monkeypatch, action, replacement, window, descriptor_support
):
    from plugins.workflow.marketplace import transactions as transaction_module

    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    invoke = _workspace_entry_point(store, candidate, action)
    if not descriptor_support:
        monkeypatch.setattr(transaction_module, "_DIRECTORY_FD_SUPPORTED", False)
        if os.name != "nt":
            before = _snapshot(store.root)
            with pytest.raises(WorkflowMarketplaceError) as unavailable:
                invoke()
            assert unavailable.value.code == "transaction_state_invalid"
            assert _snapshot(store.root) == before
            return
    destination_before = _snapshot(candidate.destination)
    foreign = tmp_path / "foreign-authority"
    foreign.mkdir(mode=0o700)
    (foreign / "evidence").write_bytes(b"foreign bytes")
    (foreign / "marketplace.lock").touch(mode=0o600)
    foreign_before = _snapshot(foreign)
    foreign_metadata = foreign.stat()
    displaced = tmp_path / "displaced-authority"
    original_metadata = None
    original_before = None
    original_entries = None

    def swap():
        nonlocal foreign, original_metadata, original_before, original_entries
        original_metadata = store.root.stat()
        original_before = _snapshot(store.root)
        original_entries = sorted(path.name for path in store.root.iterdir())
        store.root.rename(displaced)
        if replacement == "symlink":
            store.root.symlink_to(foreign, target_is_directory=True)
        else:
            foreign.rename(store.root)
            foreign = store.root

    owner = store._shared_store if window == "first_validation" else store
    method = (
        "_ensure_private_root"
        if window == "first_validation"
        else "_ensure_workflow_roots"
    )
    original = getattr(owner, method)

    def replace_after_return():
        result = original()
        if not displaced.exists():
            swap()
        return result

    monkeypatch.setattr(owner, method, replace_after_return)
    failure = None
    try:
        invoke()
    except WorkflowMarketplaceError as error:
        failure = error
    # Check filesystem truth before the exception assertion so the original RED
    # distinguishes a late refusal from a refusal before foreign mutation.
    assert sorted(path.name for path in foreign.iterdir()) == [
        "evidence",
        "marketplace.lock",
    ]
    assert _snapshot(foreign) == foreign_before
    after = foreign.stat()
    assert (after.st_dev, after.st_ino, after.st_mode) == (
        foreign_metadata.st_dev,
        foreign_metadata.st_ino,
        foreign_metadata.st_mode,
    )
    assert original_metadata is not None
    after = displaced.stat()
    assert (after.st_dev, after.st_ino, after.st_mode) == (
        original_metadata.st_dev,
        original_metadata.st_ino,
        original_metadata.st_mode,
    )
    assert _snapshot(displaced) == original_before
    assert sorted(path.name for path in displaced.iterdir()) == original_entries
    assert _snapshot(candidate.destination) == destination_before
    assert failure is not None and failure.code == "transaction_state_invalid"


@pytest.mark.parametrize(
    "action", ["prepare", "recover", "atomic_install", "atomic_remove"]
)
@pytest.mark.parametrize("descriptor_support", [True, False])
def test_original_workspace_authority_settles_normally(
    tmp_path, monkeypatch, action, descriptor_support
):
    from plugins.workflow.marketplace import transactions as transaction_module

    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    invoke = _workspace_entry_point(store, candidate, action)
    original_identity = store._shared_store._ensure_private_root()
    if not descriptor_support:
        monkeypatch.setattr(transaction_module, "_DIRECTORY_FD_SUPPORTED", False)
        if os.name != "nt":
            before = _snapshot(store.root)
            with pytest.raises(WorkflowMarketplaceError) as unavailable:
                invoke()
            assert unavailable.value.code == "transaction_state_invalid"
            assert _snapshot(store.root) == before
            return
    result = invoke()
    metadata = store.root.stat()
    assert (metadata.st_dev, metadata.st_ino) == original_identity
    if action == "prepare":
        assert (
            store.inspect_token(result.token, actor="alice", profile="p1").identity
            == candidate.identity
        )
        assert (
            load_distribution(result.staging_path).digest
            == candidate.distribution.digest
        )
    elif action == "atomic_install":
        assert store.installed_store.get(candidate.identity) == result
        assert (
            load_distribution(candidate.destination).digest
            == candidate.distribution.digest
        )
    else:
        assert not candidate.destination.exists()
        assert store.list_journals() == ()
    assert store.staging_root.is_dir() and store.quarantine_root.is_dir()


@pytest.mark.parametrize("action", ["prepare", "recover"])
@pytest.mark.parametrize("replacement", ["symlink", "directory"])
def test_defense_in_depth_workspace_mkdir_after_parent_open(
    tmp_path, monkeypatch, action, replacement
):
    """A useful supported-host defense, not atomic mkdir-and-open semantics."""
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    foreign = tmp_path / "foreign-authority"
    foreign.mkdir(mode=0o700)
    (foreign / "evidence").write_bytes(b"foreign bytes")
    before = _snapshot(foreign)
    foreign_identity = foreign.stat()
    displaced = tmp_path / "displaced-authority"
    original_mkdir = os.mkdir
    attempted = False
    blocked_by_guard = False
    original_identity = None

    def replace_at_mkdir(path, *args, **kwargs):
        nonlocal attempted, blocked_by_guard, foreign, original_identity
        if Path(path).name == ".staging" and not attempted:
            attempted = True
            original_identity = store.root.stat()
            try:
                store.root.rename(displaced)
            except PermissionError:
                # A native Windows no-delete-share guard prevents the swap.
                assert os.name == "nt"
                blocked_by_guard = True
            else:
                if replacement == "symlink":
                    store.root.symlink_to(foreign, target_is_directory=True)
                else:
                    foreign.rename(store.root)
                    foreign = store.root
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", replace_at_mkdir)
    error = None
    try:
        _workspace_entry_point(store, candidate, action)()
    except WorkflowMarketplaceError as failure:
        error = failure
    assert attempted
    assert sorted(path.name for path in foreign.iterdir()) == ["evidence"]
    assert _snapshot(foreign) == before
    after = foreign.stat()
    assert (after.st_dev, after.st_ino, after.st_mode) == (
        foreign_identity.st_dev,
        foreign_identity.st_ino,
        foreign_identity.st_mode,
    )
    assert original_identity is not None
    original = store.root if blocked_by_guard else displaced
    after = original.stat()
    assert (after.st_dev, after.st_ino, after.st_mode) == (
        original_identity.st_dev,
        original_identity.st_ino,
        original_identity.st_mode,
    )
    if blocked_by_guard:
        assert error is None
    else:
        assert error is not None and error.code == "transaction_state_invalid"
        assert sorted(path.name for path in original.iterdir()) == [
            ".staging",
            "marketplace.lock",
        ]
        assert list((original / ".staging").iterdir()) == []


@pytest.mark.parametrize(
    "action", ["prepare", "recover", "atomic_install", "atomic_remove"]
)
@pytest.mark.parametrize(
    "damage", ["lock_symlink", *(["public_mode"] if os.name != "nt" else [])]
)
def test_retained_parent_authority_revalidates_permissions_and_lock(
    tmp_path, monkeypatch, action, damage
):
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    invoke = _workspace_entry_point(store, candidate, action)
    original = store._ensure_workflow_roots
    foreign_lock = tmp_path / "foreign.lock"
    foreign_lock.write_bytes(b"foreign lock bytes")
    destination_before = _snapshot(candidate.destination)

    def damage_after_creation():
        identity = original()
        if damage == "public_mode":
            store.root.chmod(0o755)
        else:
            store.lock_path.unlink()
            store.lock_path.symlink_to(foreign_lock)
        return identity

    monkeypatch.setattr(store, "_ensure_workflow_roots", damage_after_creation)
    with pytest.raises(WorkflowMarketplaceError) as error:
        invoke()
    assert error.value.code == "transaction_state_invalid"
    assert _snapshot(candidate.destination) == destination_before
    assert foreign_lock.read_bytes() == b"foreign lock bytes"
    if action == "prepare":
        assert not store.path.exists()
        assert list(store.staging_root.iterdir()) == []


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


@pytest.mark.parametrize("marker_state", ["missing", "changed", "mismatched", "intact"])
def test_final_phase_missing_marker_at_prepared_publication_is_retained(
    tmp_path, monkeypatch, marker_state
):
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    original = store._write_prepared
    observed = {}

    def publication(*args, **kwargs):
        (envelope,) = store.staging_root.iterdir()
        if marker_state == "missing":
            (envelope / "owner.json").unlink()
        elif marker_state == "changed":
            (envelope / "owner.json").write_bytes(b"unproven marker")
        elif marker_state == "mismatched":
            marker = json.loads((envelope / "owner.json").read_bytes())
            marker["transactionId"] = "f" * 32
            (envelope / "owner.json").write_text(json.dumps(marker))
        observed["envelope"] = envelope
        observed["bytes"] = _snapshot(envelope)
        if marker_state == "intact":
            raise OSError("ordinary publication failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "_write_prepared", publication)
    with pytest.raises(WorkflowMarketplaceError):
        store.prepare(candidate, review_digest=REVIEW, actor="alice", profile="p1")
    assert not store.path.exists()
    assert not candidate.destination.exists()
    if marker_state == "intact":
        assert not observed["envelope"].exists()
    else:
        assert observed["envelope"].is_dir(), "missing-marker evidence must be retained"
        assert _snapshot(observed["envelope"]) == observed["bytes"]


@pytest.mark.parametrize("action", ["update", "remove"])
@pytest.mark.parametrize("damage", ["journal", "envelope", "none"])
def test_final_phase_rollback_entry_rechecks_authority(
    tmp_path, monkeypatch, action, damage
):
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    installed = _install(store, candidate)
    if action == "update":
        candidate = _candidate(tmp_path, _package(tmp_path, "2.0.0"))
        prepared = _consume(store, candidate)
    else:
        _, prepared = _authorize_remove(store, installed)
    helper = "_rollback_remove" if action == "remove" else "_rollback_install"
    original = getattr(store, helper)
    observed = {}

    def rollback(*args, **kwargs):
        envelope = store.quarantine_root / prepared.transaction_id
        if damage == "journal":
            payload = json.loads(store.journal_path.read_bytes())
            payload["journals"][0]["phase"] = f"{prepared.operation}_consumed"
            store.journal_path.write_text(json.dumps(payload))
            observed["evidence"] = store.journal_path
            observed["bytes"] = store.journal_path.read_bytes()
        elif damage == "envelope":
            displaced = tmp_path / "displaced-envelope"
            envelope.rename(displaced)
            shutil.copytree(displaced, envelope)
            observed["evidence"] = envelope
            observed["bytes"] = _snapshot(envelope)
        observed["installed"] = _snapshot(candidate.destination)
        return original(*args, **kwargs)

    def fault(point):
        if point == "after_backup_move":
            raise RuntimeError("ordinary named-phase failure requiring rollback")

    monkeypatch.setattr(store, helper, rollback)
    with pytest.raises((WorkflowMarketplaceError, RuntimeError)):
        mutation = store.atomic_remove if action == "remove" else store.atomic_install
        mutation(prepared, review_digest=REVIEW, fault=fault)
    assert observed
    if damage == "none":
        assert store.installed_store.get(installed.identity) == installed
        assert _snapshot(candidate.destination)
        assert store.list_journals() == ()
    else:
        assert _snapshot(candidate.destination) == observed["installed"], (
            "rollback must not mutate installed bytes after authority changes"
        )
        evidence = observed["evidence"]
        assert (
            _snapshot(evidence) if evidence.is_dir() else evidence.read_bytes()
        ) == observed["bytes"]


@pytest.mark.parametrize("action", ["update", "remove"])
@pytest.mark.parametrize("damage", ["journal", "envelope", "none"])
def test_final_phase_restart_recovery_entry_rechecks_authority(
    tmp_path, monkeypatch, action, damage
):
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    installed = _install(store, candidate)
    if action == "update":
        candidate = _candidate(tmp_path, _package(tmp_path, "2.0.0"))
        prepared = _consume(store, candidate)
    else:
        _, prepared = _authorize_remove(store, installed)

    def crash(point):
        if point == "after_backup_move":
            raise SimulatedCrash(point)

    with pytest.raises(SimulatedCrash):
        mutation = store.atomic_remove if action == "remove" else store.atomic_install
        mutation(prepared, review_digest=REVIEW, fault=crash)
    restarted = MarketplaceTransactionStore(tmp_path)
    helper = "_recover_remove" if action == "remove" else "_recover_install"
    original = getattr(restarted, helper)
    observed = {}

    def recovery(journal, **kwargs):
        if damage == "journal":
            state = json.loads(restarted.journal_path.read_bytes())
            state["journals"][0]["phase"] = f"{prepared.operation}_consumed"
            restarted.journal_path.write_text(json.dumps(state))
        elif damage == "envelope":
            envelope = restarted.quarantine_root / prepared.transaction_id
            displaced = tmp_path / "displaced-recovery-envelope"
            envelope.rename(displaced)
            shutil.copytree(displaced, envelope)
        observed["installed"] = _snapshot(candidate.destination)
        return original(journal, **kwargs)

    monkeypatch.setattr(restarted, helper, recovery)
    recovered = restarted.recover_transactions()
    assert observed
    if damage == "none":
        assert recovered == (prepared.transaction_id,)
        assert restarted.installed_store.get(installed.identity) == installed
        assert _snapshot(candidate.destination)
    else:
        assert (
            _snapshot(candidate.destination) == observed["installed"],
            recovered,
        ) == (True, ()), (
            "changed recovery authority cannot mutate installed bytes or claim recovery success"
        )


@pytest.mark.parametrize("action", ["install", "update", "remove"])
@pytest.mark.parametrize("restart", [False, True], ids=["rollback", "restart"])
@pytest.mark.parametrize(
    "damage",
    [
        "root",
        "installed_parent",
        "journal",
        "envelope",
        "marker",
        "package",
        "provenance",
        "none",
    ],
)
def test_final_phase_settlement_entry_preserves_changed_authority(
    tmp_path, monkeypatch, action, restart, damage
):
    """The rollback/restart helper entry is an observable phase, not a syscall gap."""
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    installed = None if action == "install" else _install(store, candidate)
    original_bytes = _snapshot(candidate.destination)
    if action == "remove":
        _, prepared = _authorize_remove(store, installed)
    else:
        candidate = _candidate(tmp_path, _package(tmp_path, "2.0.0"))
        prepared = _consume(store, candidate)
    observed = {}
    point = "after_candidate_swap" if action == "install" else "after_backup_move"

    def fault(phase):
        if phase == point:
            if restart:
                raise SimulatedCrash(phase)
            raise RuntimeError("ordinary settlement failure")

    mutation = store.atomic_remove if action == "remove" else store.atomic_install
    if restart:
        with pytest.raises(SimulatedCrash):
            mutation(prepared, review_digest=REVIEW, fault=fault)
        store = MarketplaceTransactionStore(tmp_path)
    helper = ("_recover_" if restart else "_rollback_") + (
        "remove" if action == "remove" else "install"
    )
    original = getattr(store, helper)

    def settlement(*args, **kwargs):
        envelope = (
            store.staging_root if action == "install" else store.quarantine_root
        ) / prepared.transaction_id
        package = candidate.destination if action == "install" else envelope / "package"
        if damage in {"root", "installed_parent", "envelope"}:
            target = {
                "root": store.root,
                "installed_parent": candidate.destination.parent,
                "envelope": envelope,
            }[damage]
            displaced = tmp_path / "displaced-authority"
            target.rename(displaced)
            shutil.copytree(displaced, target)
            observed["displaced"] = (
                displaced,
                _snapshot(displaced),
                displaced.stat().st_ino,
            )
            observed["replacement"] = (target, target.stat().st_ino)
        elif damage == "journal":
            state = json.loads(store.journal_path.read_bytes())
            state["journals"][0]["phase"] = f"{prepared.operation}_consumed"
            store.journal_path.write_text(json.dumps(state))
        elif damage == "marker":
            (envelope / "owner.json").unlink()
        elif damage == "package":
            (package / "workflow-package.yaml").write_bytes(b"changed package")
        elif damage == "provenance":
            if installed is not None:
                store.installed_store.remove(installed.identity, expected=installed)
            else:
                store.installed_store.put(store._candidate_provenance(prepared))
        observed["installed"] = _snapshot(candidate.destination)
        observed["provenance"] = store.installed_store._snapshot()
        observed["scratch"] = (
            _snapshot(store.staging_root),
            _snapshot(store.quarantine_root),
        )
        observed["journal"] = store.journal_path.read_bytes()
        return original(*args, **kwargs)

    monkeypatch.setattr(store, helper, settlement)
    if restart:
        try:
            recovered = store.recover_transactions()
        except WorkflowMarketplaceError as error:
            assert damage == "root"
            assert error.code == "transaction_state_invalid"
            recovered = ()
    else:
        with pytest.raises((WorkflowMarketplaceError, RuntimeError)):
            mutation(prepared, review_digest=REVIEW, fault=fault)
        recovered = ()
    assert observed
    if damage == "none":
        assert recovered == ((prepared.transaction_id,) if restart else ())
        assert _snapshot(candidate.destination) == original_bytes
        assert store._current_provenance(candidate.identity) == installed
        assert store.list_journals() == ()
    else:
        assert recovered == ()
        assert _snapshot(candidate.destination) == observed["installed"]
        assert store.installed_store._snapshot() == observed["provenance"]
        assert (
            _snapshot(store.staging_root),
            _snapshot(store.quarantine_root),
        ) == observed["scratch"]
        assert store.journal_path.read_bytes() == observed["journal"]
        if "displaced" in observed:
            displaced, content, inode = observed["displaced"]
            assert _snapshot(displaced) == content
            assert displaced.stat().st_ino == inode
            target, inode = observed["replacement"]
            assert target.stat().st_ino == inode


@pytest.mark.parametrize(
    ("action", "restart", "boundary"),
    [
        (action, restart, boundary)
        for action in ("install", "update", "remove")
        for restart in (False, True)
        for boundary in (
            "move",
            "provenance_write",
            "cleanup",
            "retirement",
            "publication",
            "journal_write",
        )
        # An uncommitted first install has no provenance record to restore.
        if not (action == "install" and restart and boundary == "provenance_write")
        and not (restart and boundary == "publication")
    ],
)
@pytest.mark.parametrize("damage", ["journal", "none"])
def test_final_phase_late_settlement_revalidates_before_mutation(
    tmp_path, monkeypatch, action, restart, boundary, damage
):
    """Inject at named restore/cleanup/retirement helpers, after dispatch validation."""
    import plugins.workflow.marketplace.transactions as transaction_module

    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    installed = None if action == "install" else _install(store, candidate)
    original_bytes = _snapshot(candidate.destination)
    if action == "remove":
        _, prepared = _authorize_remove(store, installed)
    else:
        candidate = _candidate(tmp_path, _package(tmp_path, "2.0.0"))
        prepared = _consume(store, candidate)
    point = "after_candidate_swap" if action == "install" else "after_backup_move"
    active = False

    def fault(phase):
        nonlocal active
        if phase == point:
            if restart:
                raise SimulatedCrash(phase)
            if boundary == "publication":
                active = True
            raise RuntimeError("ordinary settlement failure")

    mutation = store.atomic_remove if action == "remove" else store.atomic_install
    if restart:
        with pytest.raises(SimulatedCrash):
            mutation(prepared, review_digest=REVIEW, fault=fault)
        store = MarketplaceTransactionStore(tmp_path)
    helper = ("_recover_" if restart else "_rollback_") + (
        "remove" if action == "remove" else "install"
    )
    original = getattr(store, helper)
    observed = {}

    def settlement(*args, **kwargs):
        nonlocal active
        active = True
        return original(*args, **kwargs)

    monkeypatch.setattr(store, helper, settlement)
    owner = (
        transaction_module._TransactionPhases
        if boundary in {"move", "provenance_write"}
        else store
    )
    target = {
        "cleanup": "_remove_owned_envelope",
        "retirement": "_delete_journal",
        "publication": "_replace_journal",
        "journal_write": "_write_journals",
    }.get(boundary, boundary)
    actual = getattr(owner, target)

    def late_phase(*args, **kwargs):
        if active and not observed:
            if damage == "journal":
                state = json.loads(store.journal_path.read_bytes())
                state["journals"][0]["phase"] = f"{prepared.operation}_consumed"
                store.journal_path.write_text(json.dumps(state))
            observed["installed"] = _snapshot(candidate.destination)
            observed["provenance"] = store.installed_store._snapshot()
            observed["scratch"] = (
                _snapshot(store.staging_root),
                _snapshot(store.quarantine_root),
            )
            observed["journal"] = store.journal_path.read_bytes()
        return actual(*args, **kwargs)

    monkeypatch.setattr(owner, target, late_phase)
    if restart:
        recovered = store.recover_transactions()
    else:
        with pytest.raises((WorkflowMarketplaceError, RuntimeError)):
            mutation(prepared, review_digest=REVIEW, fault=fault)
        recovered = ()
    assert observed
    if damage == "none":
        assert recovered == ((prepared.transaction_id,) if restart else ())
        assert _snapshot(candidate.destination) == original_bytes
        assert store._current_provenance(candidate.identity) == installed
        assert store.list_journals() == ()
    else:
        assert recovered == ()
        assert _snapshot(candidate.destination) == observed["installed"]
        assert store.installed_store._snapshot() == observed["provenance"]
        assert (
            _snapshot(store.staging_root),
            _snapshot(store.quarantine_root),
        ) == observed["scratch"]
        assert store.journal_path.read_bytes() == observed["journal"]


@pytest.mark.parametrize(
    "damage",
    [
        "root",
        "staging",
        "envelope",
        "missing",
        "malformed",
        "mismatched",
        "changed",
        "none",
    ],
)
def test_final_phase_abandoned_cleanup_retains_changed_yielded_authority(
    tmp_path, monkeypatch, damage
):
    """The validated-candidate yield is the handoff into abandoned cleanup."""
    now = datetime(2026, 9, 7, tzinfo=timezone.utc)
    store = MarketplaceTransactionStore(tmp_path, clock=lambda: now)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    prepared = store.prepare(
        candidate, review_digest=REVIEW, actor="alice", profile="p1"
    )
    restarted = MarketplaceTransactionStore(
        tmp_path, clock=lambda: now + timedelta(hours=1)
    )
    original = restarted._abandoned_staging_candidates
    observed = {}
    envelope = prepared.staging_path.parent

    def handoff(**kwargs):
        for item in original(**kwargs):
            if damage in {"root", "staging", "envelope"}:
                target = {
                    "root": restarted.root,
                    "staging": restarted.staging_root,
                    "envelope": envelope,
                }[damage]
                displaced = tmp_path / "displaced-abandoned"
                target.rename(displaced)
                shutil.copytree(displaced, target)
                observed["displaced"] = (
                    displaced,
                    _snapshot(displaced),
                    displaced.stat().st_ino,
                )
            elif damage == "missing":
                (envelope / "owner.json").unlink()
            elif damage == "malformed":
                (envelope / "owner.json").write_bytes(b"not a marker")
            elif damage in {"changed", "mismatched"}:
                marker = json.loads((envelope / "owner.json").read_bytes())
                if damage == "changed":
                    marker["createdAt"] = "2026-09-07T00:00:01Z"
                else:
                    marker["transactionId"] = "f" * 32
                (envelope / "owner.json").write_text(json.dumps(marker))
            observed["bytes"] = _snapshot(envelope)
            observed["inode"] = envelope.stat().st_ino
            yield item

    monkeypatch.setattr(restarted, "_abandoned_staging_candidates", handoff)
    if damage == "none":
        assert restarted.recover_transactions() == ()
        assert observed
        assert not envelope.exists()
    else:
        with pytest.raises(WorkflowMarketplaceError) as error:
            restarted.recover_transactions()
        assert error.value.code in {
            "transaction_recovery_inspection_incomplete",
            "transaction_recovery_ambiguous",
            "transaction_state_invalid",
        }
        assert _snapshot(envelope) == observed["bytes"]
        assert envelope.stat().st_ino == observed["inode"]
        if "displaced" in observed:
            displaced, content, inode = observed["displaced"]
            assert _snapshot(displaced) == content
            assert displaced.stat().st_ino == inode
    assert not candidate.destination.exists()
    assert restarted._current_provenance(candidate.identity) is None


@pytest.mark.parametrize(
    "marker_state", ["missing", "malformed", "mismatched", "intact"]
)
def test_final_phase_preparation_cleanup_entry_requires_exact_marker(
    tmp_path, monkeypatch, marker_state
):
    import plugins.workflow.marketplace.transactions as transaction_module

    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    original = transaction_module._PreparationWorkspace.cleanup
    observed = {}

    def fail_publication(*args, **kwargs):
        raise OSError("ordinary publication failure")

    def cleanup(workspace):
        envelope = workspace.envelope
        assert envelope is not None
        marker_path = envelope / "owner.json"
        if marker_state == "missing":
            marker_path.unlink()
        elif marker_state == "malformed":
            marker_path.write_bytes(b"unproven marker")
        elif marker_state == "mismatched":
            marker = json.loads(marker_path.read_bytes())
            marker["transactionId"] = "f" * 32
            marker_path.write_text(json.dumps(marker))
        observed["envelope"] = envelope
        observed["bytes"] = _snapshot(envelope)
        observed["inode"] = envelope.stat().st_ino
        return original(workspace)

    monkeypatch.setattr(store, "_write_prepared", fail_publication)
    monkeypatch.setattr(transaction_module._PreparationWorkspace, "cleanup", cleanup)
    with pytest.raises(WorkflowMarketplaceError) as error:
        store.prepare(candidate, review_digest=REVIEW, actor="alice", profile="p1")
    envelope = observed["envelope"]
    if marker_state == "intact":
        assert error.value.code == "transaction_state_write_failed"
        assert not envelope.exists()
    else:
        assert error.value.code == "transaction_recovery_inspection_incomplete"
        assert _snapshot(envelope) == observed["bytes"]
        assert envelope.stat().st_ino == observed["inode"]
    assert not store.path.exists()
    assert not candidate.destination.exists()


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("action", ["install", "update", "remove"])
@pytest.mark.parametrize("damage", [True, False])
@pytest.mark.parametrize("boundary", ["instance", "inherited"])
def test_final_mutator_entry_preserves_phase_authority(
    tmp_path, monkeypatch, restart, action, damage, boundary
):
    store = MarketplaceTransactionStore(tmp_path)
    candidate = _candidate(tmp_path, _package(tmp_path, "1.0.0"))
    installed = None if action == "install" else _install(store, candidate)
    if action == "remove":
        _, prepared = _authorize_remove(store, installed)
    else:
        candidate = _candidate(tmp_path, _package(tmp_path, "2.0.0"))
        prepared = _consume(store, candidate)
    point = (
        "after_provenance_remove" if action == "remove" else "after_provenance_write"
    )
    # Force rollback authority for restart after a provenance write by crashing in
    # the already-published rollback helper, before its first mutation.
    helper = "_rollback_remove" if action == "remove" else "_rollback_install"
    if restart:

        def crash(*args, **kwargs):
            raise SimulatedCrash()

        monkeypatch.setattr(store, helper, crash)

    def fault(phase):
        if phase == point:
            raise RuntimeError("ordinary failure after provenance write")

    mutate = store.atomic_remove if action == "remove" else store.atomic_install
    if restart:
        with pytest.raises(SimulatedCrash):
            mutate(prepared, review_digest=REVIEW, fault=fault)
        store = MarketplaceTransactionStore(tmp_path)
    target = (
        ("remove" if action == "install" else "put") if restart else "_restore_snapshot"
    )
    owner = store.installed_store if boundary == "instance" else InstalledPackageStore
    actual = getattr(owner, target)
    observed = {}

    def entry(*args, **kwargs):
        if damage:
            value = json.loads(store.journal_path.read_bytes())
            value["journals"][0]["phase"] = f"{prepared.operation}_consumed"
            store.journal_path.write_text(json.dumps(value))
        observed["provenance"] = store.installed_store._snapshot()
        observed["installed"] = _snapshot(candidate.destination)
        observed["journal"] = store.journal_path.read_bytes()
        return actual(*args, **kwargs)

    monkeypatch.setattr(owner, target, entry)
    if restart:
        result = store.recover_transactions()
    else:
        with pytest.raises((WorkflowMarketplaceError, RuntimeError)):
            mutate(prepared, review_digest=REVIEW, fault=fault)
        result = ()
    assert observed
    if damage:
        assert result == ()
        assert (
            store.installed_store._snapshot() == observed["provenance"],
            _snapshot(candidate.destination) == observed["installed"],
            store.journal_path.read_bytes() == observed["journal"],
        ) == (True, True, True), (
            "provenance, package bytes and journal must remain unchanged after entry"
        )
        # An ordinary read must remain usable after the failed guarded mutation;
        # a leaked phase context would reject it against the changed journal.
        expected = next(
            (
                item.provenance
                for item in observed["provenance"][1].packages
                if item.provenance.identity == candidate.identity
            ),
            None,
        )
        assert store._current_provenance(candidate.identity) == expected
    else:
        assert result == ((prepared.transaction_id,) if restart else ())
        assert store._current_provenance(candidate.identity) == installed
        assert store.list_journals() == ()
