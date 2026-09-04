"""Verified workflow marketplace catalog refresh and cache contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest

from plugins.workflow.marketplace.catalog import WorkflowMarketplaceCatalog
from plugins.workflow.marketplace.git import WorkflowGitFetcher
from plugins.workflow.marketplace.package import WorkflowMarketplaceError
from plugins.workflow.marketplace.source_store import WorkflowSourceStore


FIXTURE_REPOSITORY = Path(__file__).parent / "fixtures" / "marketplace" / "repository"


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip().lower()


def _bare_repository(
    tmp_path: Path,
    *,
    mutation=None,
    subdirectory: str | None = None,
) -> tuple[Path, str]:
    work = tmp_path / "work"
    work.mkdir()
    target = work / subdirectory if subdirectory else work
    if subdirectory:
        target.mkdir(parents=True)
    shutil.copytree(FIXTURE_REPOSITORY, target, dirs_exist_ok=True)
    if mutation is not None:
        mutation(target)
    _git(work, "init", "--initial-branch=main")
    _git(work, "config", "user.email", "marketplace@example.test")
    _git(work, "config", "user.name", "Marketplace Test")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "publish")
    commit = _git(work, "rev-parse", "HEAD")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "clone", "--bare", str(work), str(remote))
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    return remote, commit


def _catalog(tmp_path: Path) -> WorkflowMarketplaceCatalog:
    return WorkflowMarketplaceCatalog(
        WorkflowSourceStore(tmp_path / "hermes-home"),
        WorkflowGitFetcher(),
    )


def test_refresh_verifies_multiple_packages_and_searches_exact_commit(
    tmp_path: Path,
) -> None:
    remote, commit = _bare_repository(tmp_path)
    catalog = _catalog(tmp_path)
    catalog.add_source("company", remote.as_uri())

    refreshed = catalog.refresh_source("company")
    results = catalog.search("support")
    inspected = catalog.inspect("company/laptop-support")

    assert refreshed.state == "fresh"
    assert refreshed.resolved_commit == commit
    assert refreshed.package_count == 2
    assert [result.identifier for result in results] == ["company/laptop-support"]
    assert results[0].resolved_commit == commit
    assert inspected.identifier == "company/laptop-support"
    assert inspected.package_digest == results[0].package_digest
    assert inspected.state == "fresh"


def test_verified_catalog_cache_survives_a_new_service_instance(tmp_path: Path) -> None:
    remote, commit = _bare_repository(tmp_path)
    first = _catalog(tmp_path)
    first.add_source("company", remote.as_uri())
    first.refresh_source("company")

    second = _catalog(tmp_path)

    assert [item.identifier for item in second.search("")] == [
        "company/inbox-productivity",
        "company/laptop-support",
    ]
    assert {item.resolved_commit for item in second.search("")} == {commit}


def test_failed_refresh_preserves_last_verified_catalog_and_redacts_error(
    tmp_path: Path,
) -> None:
    remote, commit = _bare_repository(tmp_path)
    catalog = _catalog(tmp_path)
    catalog.add_source("company", remote.as_uri())
    first = catalog.refresh_source("company")

    class AuthenticationFailure:
        def fetch(self, *args, **kwargs):
            raise WorkflowMarketplaceError(
                "source_authentication_failed",
                (
                    "authentication failed for "
                    "https://token-user:token-secret@example.test/repo.git"
                ),
            )

    catalog.git_fetcher = AuthenticationFailure()
    second = catalog.refresh_source("company")

    assert first.resolved_commit == commit
    assert second.state == "stale"
    assert second.diagnostic_code == "source_authentication_failed"
    assert catalog.search("support")[0].resolved_commit == first.resolved_commit
    assert catalog.search("support")[0].state == "stale"
    assert "token-user" not in repr(second)
    assert "token-secret" not in repr(second)
    assert "token-user" not in catalog.source_store.catalog_path.read_text()
    assert "token-secret" not in catalog.source_store.catalog_path.read_text()


def test_failed_refresh_replaces_ssh_password_diagnostic_before_persistence(
    tmp_path: Path,
) -> None:
    remote, _ = _bare_repository(tmp_path)
    catalog = _catalog(tmp_path)
    catalog.add_source("company", remote.as_uri())

    class AuthenticationFailure:
        def fetch(self, *args, **kwargs):
            raise WorkflowMarketplaceError(
                "source_authentication_failed",
                (
                    "authentication failed for "
                    "ssh://git:status-secret@example.test/team/repo.git"
                ),
            )

    catalog.git_fetcher = AuthenticationFailure()
    result = catalog.refresh_source("company")

    assert result.state == "authentication-failed"
    assert result.message == "marketplace source refresh failed"
    assert "status-secret" not in repr(result)
    assert "status-secret" not in catalog.source_store.catalog_path.read_text()


def test_oversized_failed_status_encoding_preserves_verified_cache(
    tmp_path: Path,
) -> None:
    remote, commit = _bare_repository(tmp_path)
    catalog = _catalog(tmp_path)
    catalog.add_source("company", remote.as_uri())
    catalog.refresh_source("company")
    before = catalog.source_store.catalog_path.read_bytes()
    catalog.source_store.max_catalog_state_bytes = len(before) + 8

    class LargeFailure:
        def fetch(self, *args, **kwargs):
            raise WorkflowMarketplaceError("source_unavailable", "x" * 4096)

    catalog.git_fetcher = LargeFailure()
    result = catalog.refresh_source("company")

    assert result.state == "unavailable"
    assert result.diagnostic_code == "catalog_state_size_limit"
    assert result.resolved_commit is None
    assert catalog.source_store.catalog_path.read_bytes() == before
    assert catalog.search("")[0].resolved_commit == commit


def test_cancelled_refresh_preserves_state_and_verified_cache_bytes(
    tmp_path: Path,
) -> None:
    remote, commit = _bare_repository(tmp_path)
    catalog = _catalog(tmp_path)
    catalog.add_source("company", remote.as_uri())
    catalog.refresh_source("company")
    before = catalog.source_store.catalog_path.read_bytes()

    cancelled = catalog.refresh_source("company", cancelled=lambda: True)

    assert cancelled.state == "cancelled"
    assert cancelled.resolved_commit == commit
    assert catalog.source_store.catalog_path.read_bytes() == before


def test_refresh_from_a_direct_subdirectory_verifies_that_catalog_root(
    tmp_path: Path,
) -> None:
    remote, commit = _bare_repository(tmp_path, subdirectory="catalog")
    catalog = _catalog(tmp_path)
    catalog.add_source("company", f"{remote.as_uri()}#catalog")

    result = catalog.refresh_source("company")

    assert result.state == "fresh"
    assert result.resolved_commit == commit
    assert catalog.inspect("company/inbox-productivity").version == "2.0.0"


@pytest.mark.parametrize(
    ("mutation", "diagnostic_code", "state"),
    [
        (
            lambda root: shutil.rmtree(root / ".well-known"),
            "package_index_invalid",
            "malformed",
        ),
        (
            lambda root: (root / ".well-known/hermes-workflows/index.json").write_text(
                "{not-json", encoding="utf-8"
            ),
            "package_index_invalid",
            "malformed",
        ),
        (
            lambda root: _replace_index_digest(root, "0" * 64),
            "package_digest_mismatch",
            "malformed",
        ),
        (
            lambda root: _replace_index_version(root, 2),
            "package_contract_unsupported",
            "incompatible",
        ),
        (
            lambda root: _replace_index_path(root, "packages/.git/private"),
            "package_index_invalid",
            "malformed",
        ),
    ],
)
def test_missing_malformed_stale_or_incompatible_index_fails_without_cache(
    tmp_path: Path,
    mutation,
    diagnostic_code: str,
    state: str,
) -> None:
    remote, _ = _bare_repository(tmp_path, mutation=mutation)
    catalog = _catalog(tmp_path)
    catalog.add_source("company", remote.as_uri())

    result = catalog.refresh_source("company")

    assert result.state == state
    assert result.diagnostic_code == diagnostic_code
    assert result.resolved_commit is None
    assert catalog.search("") == ()


def _replace_index_digest(root: Path, digest: str) -> None:
    path = root / ".well-known" / "hermes-workflows" / "index.json"
    value = json.loads(path.read_bytes())
    value["packages"][0]["packageDigest"] = digest
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _replace_index_version(root: Path, version: int) -> None:
    path = root / ".well-known" / "hermes-workflows" / "index.json"
    value = json.loads(path.read_bytes())
    value["schemaVersion"] = version
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _replace_index_path(root: Path, package_path: str) -> None:
    path = root / ".well-known" / "hermes-workflows" / "index.json"
    value = json.loads(path.read_bytes())
    value["packages"][0]["packagePath"] = package_path
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def test_disabled_source_does_not_fetch_or_expose_cached_results(
    tmp_path: Path,
) -> None:
    remote, commit = _bare_repository(tmp_path)
    catalog = _catalog(tmp_path)
    catalog.add_source("company", remote.as_uri())
    catalog.refresh_source("company")
    catalog.source_store.set_enabled("company", False)

    result = catalog.refresh_source("company")

    assert result.state == "disabled"
    assert result.resolved_commit == commit
    assert result.package_count == 2
    assert catalog.search("") == ()
    with pytest.raises(WorkflowMarketplaceError, match="catalog_package_not_found"):
        catalog.inspect("company/laptop-support")


def test_search_is_deterministic_bounded_and_uses_verified_projection(
    tmp_path: Path,
) -> None:
    remote, _ = _bare_repository(tmp_path)
    catalog = _catalog(tmp_path)
    catalog.add_source("zeta", remote.as_uri())
    catalog.add_source("alpha", remote.as_uri())
    catalog.refresh_source("zeta")
    catalog.refresh_source("alpha")

    results = catalog.search("", limit=3)

    assert [item.identifier for item in results] == [
        "alpha/inbox-productivity",
        "alpha/laptop-support",
        "zeta/inbox-productivity",
    ]
    with pytest.raises(WorkflowMarketplaceError, match="catalog_query_invalid"):
        catalog.search("x" * 257)
    with pytest.raises(WorkflowMarketplaceError, match="catalog_limit_invalid"):
        catalog.search("", limit=201)


def test_corrupt_catalog_cache_is_not_rewritten_by_search_or_refresh(
    tmp_path: Path,
) -> None:
    remote, _ = _bare_repository(tmp_path)
    catalog = _catalog(tmp_path)
    catalog.add_source("company", remote.as_uri())
    catalog.source_store.catalog_path.parent.mkdir(parents=True, exist_ok=True)
    corrupt = b'{"schemaVersion":1,"verified":"wrong","statuses":[]}\n'
    catalog.source_store.catalog_path.write_bytes(corrupt)

    with pytest.raises(WorkflowMarketplaceError, match="catalog_state_invalid"):
        catalog.search("")
    result = catalog.refresh_source("company")

    assert result.state == "unavailable"
    assert result.diagnostic_code == "catalog_state_invalid"
    assert catalog.source_store.catalog_path.read_bytes() == corrupt


def test_tampered_cache_with_duplicate_packages_is_rejected(tmp_path: Path) -> None:
    remote, _ = _bare_repository(tmp_path)
    catalog = _catalog(tmp_path)
    catalog.add_source("company", remote.as_uri())
    catalog.refresh_source("company")
    raw = json.loads(catalog.source_store.catalog_path.read_bytes())
    raw["verified"][0]["packages"].append(raw["verified"][0]["packages"][0])
    tampered = (json.dumps(raw, sort_keys=True) + "\n").encode()
    catalog.source_store.catalog_path.write_bytes(tampered)

    with pytest.raises(WorkflowMarketplaceError, match="catalog_state_invalid"):
        catalog.search("")

    assert catalog.source_store.catalog_path.read_bytes() == tampered


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["verified"][0].__setitem__("verifiedAt", "tomorrow"),
        lambda value: value["statuses"][0].__setitem__("attemptedAt", "later"),
        lambda value: value.__setitem__("statuses", []),
        lambda value: value["statuses"][0].update({
            "state": "unavailable",
            "diagnosticCode": "source_unavailable",
            "message": "repository unavailable",
        }),
        lambda value: value["statuses"][0].update({
            "state": "stale",
            "diagnosticCode": "source_authentication_failed",
            "message": (
                "authentication failed for "
                "https://alice:status-secret@example.test/repo.git"
            ),
        }),
        lambda value: value["verified"][0]["source"].__setitem__(
            "repositoryUrl", "https://example.test/different.git"
        ),
        lambda value: value["statuses"].append({
            "sourceName": "ghost",
            "state": "unavailable",
            "attemptedAt": "2026-09-04T12:00:00Z",
            "diagnosticCode": "source_unavailable",
            "message": "repository unavailable",
        }),
    ],
)
def test_tampered_cache_metadata_fails_closed_without_rewrite_or_secret_display(
    tmp_path: Path,
    mutation,
) -> None:
    remote, _ = _bare_repository(tmp_path)
    catalog = _catalog(tmp_path)
    catalog.add_source("company", remote.as_uri())
    catalog.refresh_source("company")
    raw = json.loads(catalog.source_store.catalog_path.read_bytes())
    mutation(raw)
    tampered = (json.dumps(raw, sort_keys=True) + "\n").encode()
    catalog.source_store.catalog_path.write_bytes(tampered)

    with pytest.raises(WorkflowMarketplaceError) as error:
        catalog.search("")

    assert error.value.code == "catalog_state_invalid"
    assert "status-secret" not in str(error.value)
    assert catalog.source_store.catalog_path.read_bytes() == tampered


@pytest.mark.parametrize(
    "message",
    [
        "authentication failed for ssh://git:status-secret@example.test/repo.git",
        "authentication failed for alice:status-secret@example.test/repo.git",
        "authentication failed for owner/repo?token=status-secret",
    ],
)
def test_tampered_credential_bearing_status_forms_fail_without_rewrite(
    tmp_path: Path,
    message: str,
) -> None:
    remote, _ = _bare_repository(tmp_path)
    catalog = _catalog(tmp_path)
    catalog.add_source("company", remote.as_uri())
    catalog.refresh_source("company")
    raw = json.loads(catalog.source_store.catalog_path.read_bytes())
    raw["statuses"][0].update({
        "state": "stale",
        "diagnosticCode": "source_authentication_failed",
        "message": message,
    })
    tampered = (json.dumps(raw, sort_keys=True) + "\n").encode()
    catalog.source_store.catalog_path.write_bytes(tampered)

    with pytest.raises(WorkflowMarketplaceError) as error:
        catalog.search("")

    assert error.value.code == "catalog_state_invalid"
    assert "status-secret" not in str(error.value)
    assert catalog.source_store.catalog_path.read_bytes() == tampered


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_read_only_credential_bearing_status_fails_without_chmod_or_rewrite(
    tmp_path: Path,
) -> None:
    remote, _ = _bare_repository(tmp_path)
    catalog = _catalog(tmp_path)
    catalog.add_source("company", remote.as_uri())
    catalog.refresh_source("company")
    raw = json.loads(catalog.source_store.catalog_path.read_bytes())
    raw["statuses"][0].update({
        "state": "stale",
        "diagnosticCode": "source_authentication_failed",
        "message": (
            "authentication failed for ssh://git:status-secret@example.test/repo.git"
        ),
    })
    tampered = (json.dumps(raw, sort_keys=True) + "\n").encode()
    catalog.source_store.catalog_path.write_bytes(tampered)
    catalog.source_store.catalog_path.chmod(0o400)

    with pytest.raises(WorkflowMarketplaceError) as error:
        catalog.search("")

    assert error.value.code == "catalog_state_invalid"
    assert "status-secret" not in str(error.value)
    assert catalog.source_store.catalog_path.read_bytes() == tampered
    assert stat.S_IMODE(catalog.source_store.catalog_path.stat().st_mode) == 0o400


def test_sanitized_ssh_username_status_remains_valid(tmp_path: Path) -> None:
    remote, _ = _bare_repository(tmp_path)
    catalog = _catalog(tmp_path)
    catalog.add_source("company", remote.as_uri())
    catalog.refresh_source("company")
    raw = json.loads(catalog.source_store.catalog_path.read_bytes())
    raw["statuses"][0].update({
        "state": "stale",
        "diagnosticCode": "source_unavailable",
        "message": "fatal: ssh://git@example.test/team/repo.git was unavailable",
    })
    catalog.source_store.catalog_path.write_text(
        json.dumps(raw, sort_keys=True) + "\n", encoding="utf-8"
    )

    assert catalog.search("")[0].state == "stale"


def test_source_removed_during_refresh_cannot_publish_orphaned_cache(
    tmp_path: Path,
) -> None:
    remote, _ = _bare_repository(tmp_path)
    catalog = _catalog(tmp_path)
    catalog.add_source("company", remote.as_uri())
    real_fetcher = catalog.git_fetcher

    class RemoveSourceAfterFetch:
        def fetch(self, *args, **kwargs):
            checkout = real_fetcher.fetch(*args, **kwargs)
            catalog.source_store.remove("company")
            return checkout

    catalog.git_fetcher = RemoveSourceAfterFetch()

    result = catalog.refresh_source("company")

    assert result.state == "unavailable"
    assert result.diagnostic_code == "source_changed"
    assert catalog.source_store.list_sources() == ()
    assert catalog.source_store.verified_catalogs() == ()


def test_cancellation_after_fetch_but_before_replacement_preserves_empty_cache(
    tmp_path: Path,
) -> None:
    remote, _ = _bare_repository(tmp_path)
    catalog = _catalog(tmp_path)
    catalog.add_source("company", remote.as_uri())
    real_fetcher = catalog.git_fetcher
    cancelled = False

    class CancelAfterFetch:
        def fetch(self, *args, **kwargs):
            nonlocal cancelled
            checkout = real_fetcher.fetch(*args, **kwargs)
            cancelled = True
            return checkout

    catalog.git_fetcher = CancelAfterFetch()

    result = catalog.refresh_source("company", cancelled=lambda: cancelled)

    assert result.state == "cancelled"
    assert not catalog.source_store.catalog_path.exists()
    assert catalog.search("") == ()


def test_cancellation_sampled_inside_store_lock_prevents_cache_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, _ = _bare_repository(tmp_path)
    catalog = _catalog(tmp_path)
    catalog.add_source("company", remote.as_uri())
    original = catalog.source_store.replace_verified_cache
    cancellation_requested = False
    saw_store_token = False

    def flip_at_store_entry(verified, *, attempted_at, **kwargs):
        nonlocal cancellation_requested, saw_store_token
        callback = kwargs.get("cancelled")
        saw_store_token = callback is not None
        cancellation_requested = True
        if callback is None:
            return original(verified, attempted_at=attempted_at)
        return original(
            verified,
            attempted_at=attempted_at,
            cancelled=callback,
        )

    monkeypatch.setattr(
        catalog.source_store,
        "replace_verified_cache",
        flip_at_store_entry,
    )

    result = catalog.refresh_source("company", cancelled=lambda: cancellation_requested)

    assert saw_store_token is True
    assert result.state == "cancelled"
    assert not catalog.source_store.catalog_path.exists()


def test_cancellation_sampled_after_encoding_before_atomic_cache_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugins.workflow.marketplace.source_store as source_store_module

    remote, _ = _bare_repository(tmp_path)
    catalog = _catalog(tmp_path)
    catalog.add_source("company", remote.as_uri())
    original_render = source_store_module._render_state
    cancellation_requested = False

    def flip_after_render(*args, **kwargs):
        nonlocal cancellation_requested
        rendered = original_render(*args, **kwargs)
        cancellation_requested = True
        return rendered

    monkeypatch.setattr(source_store_module, "_render_state", flip_after_render)

    result = catalog.refresh_source("company", cancelled=lambda: cancellation_requested)

    assert result.state == "cancelled"
    assert not catalog.source_store.catalog_path.exists()
