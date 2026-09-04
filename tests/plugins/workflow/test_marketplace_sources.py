"""Profile-local workflow marketplace source state contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from plugins.workflow.marketplace.catalog import WorkflowMarketplaceCatalog
from plugins.workflow.marketplace.git import WorkflowGitFetcher
from plugins.workflow.marketplace.package import WorkflowMarketplaceError
from plugins.workflow.marketplace.source_store import WorkflowSourceStore


def _prepare_private_state_root(store: WorkflowSourceStore) -> None:
    store.root.parent.mkdir(parents=True, exist_ok=True)
    store.root.mkdir(exist_ok=True)
    if os.name != "nt":
        store.root.parent.chmod(0o700)
        store.root.chmod(0o700)


def test_source_store_never_persists_credentials(tmp_path: Path) -> None:
    store = WorkflowSourceStore(tmp_path)

    with pytest.raises(WorkflowMarketplaceError, match="source_credentials_forbidden"):
        store.add(
            "private",
            "https://alice:secret@example.test/team/repo.git?token=also-secret",
        )

    assert not store.path.exists()


def test_malformed_source_identity_never_echoes_possible_credentials(
    tmp_path: Path,
) -> None:
    store = WorkflowSourceStore(tmp_path)

    with pytest.raises(WorkflowMarketplaceError) as error:
        store.add("private", "alice:secret@example.test")

    assert error.value.code == "source_invalid"
    assert "secret" not in str(error.value)
    assert "alice" not in str(error.value)
    assert not store.path.exists()


def test_source_names_and_repository_identity_are_canonicalized(tmp_path: Path) -> None:
    store = WorkflowSourceStore(tmp_path)

    source = store.add(
        " Company_Workflows ",
        "https://github.com/example/workflows/tree/main/catalog",
        ref="release",
    )

    assert source.name == "company_workflows"
    assert source.repository_url == ("https://github.com/example/workflows.git#catalog")
    assert source.ref == "release"
    assert store.get("COMPANY_WORKFLOWS") == source
    persisted = store.path.read_text(encoding="utf-8")
    assert "tree/main" not in persisted
    assert "secret" not in persisted


def test_source_names_are_unique_after_canonicalization(tmp_path: Path) -> None:
    store = WorkflowSourceStore(tmp_path)
    store.add("Company", "file:///tmp/company.git")

    with pytest.raises(WorkflowMarketplaceError, match="source_already_exists"):
        store.add(" company ", "file:///tmp/mirror.git")

    assert [source.name for source in store.list_sources()] == ["company"]


def test_source_store_is_profile_local_versioned_and_private(tmp_path: Path) -> None:
    first_home = tmp_path / "first"
    second_home = tmp_path / "second"
    first = WorkflowSourceStore(first_home)
    second = WorkflowSourceStore(second_home)

    first.add("company", "file:///tmp/company.git", ref="main")

    assert second.list_sources() == ()
    raw = json.loads(first.path.read_bytes())
    assert raw == {
        "schemaVersion": 1,
        "sources": [
            {
                "enabled": True,
                "name": "company",
                "ref": "main",
                "repositoryUrl": "file:///tmp/company.git",
            }
        ],
    }
    if os.name != "nt":
        assert first.root.stat().st_mode & 0o777 == 0o700
        assert first.root.parent.stat().st_mode & 0o777 == 0o700
        assert first.path.stat().st_mode & 0o777 == 0o600
        assert first.lock_path.stat().st_mode & 0o777 == 0o600


def test_source_store_supports_disable_remove_without_touching_other_sources(
    tmp_path: Path,
) -> None:
    store = WorkflowSourceStore(tmp_path)
    store.add("alpha", "file:///tmp/alpha.git")
    store.add("beta", "file:///tmp/beta.git")

    disabled = store.set_enabled("alpha", False)
    removed = store.remove("beta")

    assert disabled.enabled is False
    assert removed.name == "beta"
    assert store.list_sources() == (disabled,)
    with pytest.raises(WorkflowMarketplaceError, match="source_not_found"):
        store.get("beta")


def test_source_update_replaces_configuration_and_invalidates_verified_cache(
    tmp_path: Path,
) -> None:
    from tests.plugins.workflow.test_marketplace_catalog import _bare_repository

    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    first_remote, _ = _bare_repository(tmp_path / "first")
    second_remote, _ = _bare_repository(tmp_path / "second")
    store = WorkflowSourceStore(tmp_path / "home")
    catalog = WorkflowMarketplaceCatalog(store, WorkflowGitFetcher())
    catalog.add_source("company", first_remote.as_uri(), ref="main")
    assert catalog.refresh_source("company").state == "fresh"
    assert catalog.search("")

    updated = store.update(
        " COMPANY ",
        second_remote.as_uri(),
        ref=None,
        enabled=True,
    )

    assert updated.name == "company"
    assert updated.repository_url == second_remote.as_uri()
    assert updated.ref is None
    assert store.verified_catalogs() == ()
    assert store.status("company") is None
    assert catalog.search("") == ()


def test_source_update_validation_failure_preserves_exact_source_and_cache(
    tmp_path: Path,
) -> None:
    from tests.plugins.workflow.test_marketplace_catalog import _bare_repository

    remote, _ = _bare_repository(tmp_path)
    store = WorkflowSourceStore(tmp_path / "home")
    catalog = WorkflowMarketplaceCatalog(store, WorkflowGitFetcher())
    catalog.add_source("company", remote.as_uri(), ref="main")
    assert catalog.refresh_source("company").state == "fresh"
    before_sources = store.path.read_bytes()
    before_catalog = store.catalog_path.read_bytes()

    with pytest.raises(WorkflowMarketplaceError) as error:
        store.update(
            "company",
            "https://alice:top-secret@example.test/workflows.git",
            ref="release",
            enabled=False,
        )

    assert error.value.code == "source_credentials_forbidden"
    assert "top-secret" not in str(error.value)
    assert store.path.read_bytes() == before_sources
    assert store.catalog_path.read_bytes() == before_catalog


def test_enabled_only_source_update_preserves_compatible_verified_cache(
    tmp_path: Path,
) -> None:
    from tests.plugins.workflow.test_marketplace_catalog import _bare_repository

    remote, _ = _bare_repository(tmp_path)
    store = WorkflowSourceStore(tmp_path / "home")
    catalog = WorkflowMarketplaceCatalog(store, WorkflowGitFetcher())
    original = catalog.add_source("company", remote.as_uri(), ref="main")
    assert catalog.refresh_source("company").state == "fresh"
    cached = store.cached(original)
    assert cached is not None

    updated = store.update(
        "company",
        original.repository_url,
        ref=original.ref,
        enabled=False,
    )

    assert updated.enabled is False
    assert store.cached(updated) == cached


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schemaVersion":1,"sources":[],"unknown":true}\n',
        b'{"schemaVersion":2,"sources":[]}\n',
        b'{"schemaVersion":1,"sources":[],"sources":[]}\n',
        b'{"schemaVersion":1,"sources":NaN}\n',
    ],
)
def test_malformed_source_state_fails_closed_without_rewrite(
    tmp_path: Path,
    payload: bytes,
) -> None:
    store = WorkflowSourceStore(tmp_path)
    _prepare_private_state_root(store)
    store.path.write_bytes(payload)
    before = store.path.read_bytes()

    with pytest.raises(WorkflowMarketplaceError, match="source_state_invalid"):
        store.list_sources()

    assert store.path.read_bytes() == before


def test_oversized_source_state_is_rejected_before_json_parsing(tmp_path: Path) -> None:
    store = WorkflowSourceStore(tmp_path)
    _prepare_private_state_root(store)
    payload = b"{" + b" " * (store.max_source_state_bytes + 1)
    store.path.write_bytes(payload)

    with pytest.raises(WorkflowMarketplaceError, match="source_state_size_limit"):
        store.list_sources()

    assert store.path.read_bytes() == payload


def test_oversized_source_encoding_preserves_previous_state(tmp_path: Path) -> None:
    store = WorkflowSourceStore(tmp_path)
    store.add("alpha", "file:///tmp/alpha.git")
    before = store.path.read_bytes()
    store.max_source_state_bytes = len(before) + 8

    with pytest.raises(WorkflowMarketplaceError, match="source_state_size_limit"):
        store.add("beta", "file:///tmp/beta-repository-with-a-long-name.git")

    assert store.path.read_bytes() == before


def test_symlinked_state_root_is_rejected_without_writing_the_target(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    home = tmp_path / "home"
    marketplace = home / "marketplace"
    marketplace.mkdir(parents=True)
    try:
        (marketplace / "workflows").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("host does not permit directory symlinks")
    store = WorkflowSourceStore(home)

    with pytest.raises(WorkflowMarketplaceError, match="source_state_invalid"):
        store.add("company", "file:///tmp/company.git")

    assert list(outside.iterdir()) == []


def test_symlinked_hermes_home_is_rejected_before_creating_marketplace(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside-home"
    outside.mkdir()
    home = tmp_path / "home-link"
    try:
        home.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("host does not permit directory symlinks")

    with pytest.raises(WorkflowMarketplaceError, match="source_state_invalid"):
        WorkflowSourceStore(home).add("company", "file:///tmp/company.git")

    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
@pytest.mark.parametrize("target", ["root", "file"])
def test_existing_read_only_marketplace_state_is_not_chmodded_or_replaced(
    tmp_path: Path,
    target: str,
) -> None:
    store = WorkflowSourceStore(tmp_path)
    store.add("alpha", "file:///tmp/alpha.git")
    path = store.root if target == "root" else store.path
    path.chmod(0o500 if target == "root" else 0o400)
    before = store.path.read_bytes()
    before_mode = stat.S_IMODE(path.stat().st_mode)

    with pytest.raises(WorkflowMarketplaceError):
        store.add("beta", "file:///tmp/beta.git")

    assert store.path.read_bytes() == before
    assert stat.S_IMODE(path.stat().st_mode) == before_mode


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_existing_non_private_marketplace_state_is_not_replaced(
    tmp_path: Path,
) -> None:
    store = WorkflowSourceStore(tmp_path)
    store.add("alpha", "file:///tmp/alpha.git")
    before = store.path.read_bytes()
    store.path.chmod(0o644)

    with pytest.raises(WorkflowMarketplaceError, match="source_state_write_failed"):
        store.add("beta", "file:///tmp/beta.git")

    assert store.path.read_bytes() == before
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o644


def test_marketplace_state_rejects_windows_reparse_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugins.workflow.marketplace.source_store as source_store_module

    store = WorkflowSourceStore(tmp_path)
    store.home.chmod(0o700)
    store.root.parent.mkdir(mode=0o700)
    store.root.mkdir(mode=0o700)
    root_identity = (store.root.stat().st_dev, store.root.stat().st_ino)
    original = source_store_module._is_reparse_point

    def mark_root_reparse(metadata) -> bool:
        return (metadata.st_dev, metadata.st_ino) == root_identity or original(metadata)

    monkeypatch.setattr(
        source_store_module,
        "_is_reparse_point",
        mark_root_reparse,
    )

    with pytest.raises(WorkflowMarketplaceError, match="source_state_invalid"):
        store.add("company", "file:///tmp/company.git")

    assert list(store.root.iterdir()) == []


def test_marketplace_state_read_rejects_final_windows_reparse_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugins.workflow.marketplace.source_store as source_store_module

    store = WorkflowSourceStore(tmp_path)
    store.add("company", "file:///tmp/company.git")
    before = store.path.read_bytes()
    file_identity = (store.path.stat().st_dev, store.path.stat().st_ino)
    original = source_store_module._is_reparse_point

    def mark_file_reparse(metadata) -> bool:
        return (metadata.st_dev, metadata.st_ino) == file_identity or original(metadata)

    monkeypatch.setattr(
        source_store_module,
        "_is_reparse_point",
        mark_file_reparse,
    )

    with pytest.raises(WorkflowMarketplaceError, match="source_state_invalid"):
        store.list_sources()

    assert store.path.read_bytes() == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory descriptors")
def test_marketplace_write_does_not_follow_parent_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import utils as utils_module

    store = WorkflowSourceStore(tmp_path)
    store.home.chmod(0o700)
    store.root.parent.mkdir(mode=0o700)
    store.root.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    detached = tmp_path / "detached-workflows"
    real_replace = utils_module.os.replace
    swapped = False

    def swap_then_replace(source, destination, *args, **kwargs):
        nonlocal swapped
        if not swapped and kwargs.get("dst_dir_fd") is not None:
            swapped = True
            store.root.rename(detached)
            store.root.symlink_to(outside, target_is_directory=True)
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(utils_module.os, "replace", swap_then_replace)

    with pytest.raises(WorkflowMarketplaceError, match="source_state_write_failed"):
        store.add("company", "file:///tmp/company.git")

    assert swapped is True
    assert not (outside / "sources.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory identities")
def test_marketplace_write_rejects_root_replaced_after_lock_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = WorkflowSourceStore(tmp_path)
    original_write = store._write
    detached = tmp_path / "detached-workflows"
    swapped = False

    def swap_before_write(path, value, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            store.root.rename(detached)
            store.root.mkdir(mode=0o700)
        return original_write(path, value, **kwargs)

    monkeypatch.setattr(store, "_write", swap_before_write)

    with pytest.raises(WorkflowMarketplaceError, match="source_state_write_failed"):
        store.add("company", "file:///tmp/company.git")

    assert swapped is True
    assert not (store.root / "sources.json").exists()


def test_remove_does_not_change_sources_when_catalog_state_is_corrupt(
    tmp_path: Path,
) -> None:
    store = WorkflowSourceStore(tmp_path)
    store.add("company", "file:///tmp/company.git")
    before = store.path.read_bytes()
    store.catalog_path.write_bytes(b'{"schemaVersion":1,"verified":"bad"}\n')

    with pytest.raises(WorkflowMarketplaceError, match="catalog_state_invalid"):
        store.remove("company")

    assert store.path.read_bytes() == before


def test_broken_source_state_symlink_is_corruption_not_empty_state(
    tmp_path: Path,
) -> None:
    store = WorkflowSourceStore(tmp_path)
    _prepare_private_state_root(store)
    try:
        store.path.symlink_to(tmp_path / "missing-state.json")
    except OSError:
        pytest.skip("host does not permit file symlinks")

    with pytest.raises(WorkflowMarketplaceError, match="source_state_invalid"):
        store.list_sources()

    assert store.path.is_symlink()


def test_tampered_source_state_cannot_introduce_ssh_password(tmp_path: Path) -> None:
    store = WorkflowSourceStore(tmp_path)
    _prepare_private_state_root(store)
    payload = {
        "schemaVersion": 1,
        "sources": [
            {
                "enabled": True,
                "name": "company",
                "ref": None,
                "repositoryUrl": (
                    "ssh://git:persisted-secret@example.test/team/repo.git"
                ),
            }
        ],
    }
    encoded = (json.dumps(payload, sort_keys=True) + "\n").encode()
    store.path.write_bytes(encoded)

    with pytest.raises(WorkflowMarketplaceError, match="source_state_invalid"):
        store.list_sources()

    assert store.path.read_bytes() == encoded


def test_partial_remove_failure_cannot_resurrect_cache_after_readd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.plugins.workflow.test_marketplace_catalog import _bare_repository

    remote, _ = _bare_repository(tmp_path)
    store = WorkflowSourceStore(tmp_path / "home")
    catalog = WorkflowMarketplaceCatalog(store, WorkflowGitFetcher())
    catalog.add_source("company", remote.as_uri())
    catalog.refresh_source("company")
    original_write = store._write
    writes = 0

    def fail_second_write(path, value, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise WorkflowMarketplaceError(
                "source_state_write_failed", "injected source write failure"
            )
        original_write(path, value, **kwargs)

    monkeypatch.setattr(store, "_write", fail_second_write)

    with pytest.raises(WorkflowMarketplaceError, match="source_state_write_failed"):
        store.remove("company")

    assert store.get("company").name == "company"
    assert store.verified_catalogs() == ()

    monkeypatch.setattr(store, "_write", original_write)
    store.remove("company")
    catalog.add_source("company", remote.as_uri())
    assert catalog.search("") == ()
