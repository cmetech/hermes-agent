from __future__ import annotations

import json
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import shutil
import subprocess
import threading
import time

import pytest
import yaml

from hermes_cli import capability_staging
from plugins.workflow.showcase import (
    ShowcaseCatalogError,
    load_showcase_catalog,
    preflight_showcase,
)
from plugins.workflow.trust import (
    WorkflowResourceCapacityError,
    WorkflowResourceReadBudget,
)
from plugins.workflow.cli import register_cli
import plugins.workflow.showcase as showcase_module


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_catalog_has_safe_digest_verified_scenarios() -> None:
    catalog = load_showcase_catalog()

    assert set(catalog) == {
        "ai-extensions",
        "approval-gate",
        "laptop-diagnostic",
        "resilience",
        "scheduling",
    }
    assert catalog["laptop-diagnostic"].offline is True
    assert catalog["laptop-diagnostic"].requires_ai is False
    assert catalog["ai-extensions"].requires_ai is True
    assert catalog["scheduling"].interaction_mode == "schedule"
    assert all(item.package_digest for item in catalog.values())
    assert all(item.verified_bundled_provenance for item in catalog.values())
    assert all("destructive" not in item.safety_class for item in catalog.values())


@pytest.mark.parametrize(
    "node_yaml",
    [
        "  - {id: consumer, command: inspect-evidence}\n",
        "  - {id: consumer, prompt: summarize}\n",
        (
            "  - id: consumer\n"
            "    loop: {prompt: iterate, until: DONE, max_iterations: 1}\n"
        ),
        (
            "  - id: consumer\n"
            "    command: inspect-evidence\n"
            "    agents:\n"
            "      reviewer:\n"
            "        description: Review the fictional result\n"
            "        prompt: Review without network access.\n"
        ),
    ],
    ids=("command", "prompt", "loop", "inline-agent"),
)
def test_copied_non_ai_bundle_rejects_agent_backed_features(
    tmp_path: Path, node_yaml: str
) -> None:
    copied = tmp_path / "showcases"
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", copied)
    workflow = (
        copied
        / "packages/laptop-diagnostic/workflows/laptop-diagnostic.yaml"
    )
    workflow.write_text(
        "name: laptop-diagnostic\n"
        "description: Restamped non-AI classifier fixture\n"
        "nodes:\n"
        + node_yaml,
        encoding="utf-8",
    )
    manifest_path = copied / "digests.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["packages"]["laptop-diagnostic"] = showcase_module._tree_digest(
        copied / "packages/laptop-diagnostic"
    )
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ShowcaseCatalogError, match="requires_ai"):
        load_showcase_catalog(copied)


def test_exact_membership_rejects_a_fully_restamped_sixth_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authenticated_catalog = load_showcase_catalog()
    assert all(
        scenario.verified_bundled_provenance
        for scenario in authenticated_catalog.values()
    )
    expected_ids = set(authenticated_catalog)
    assert expected_ids == {
        "ai-extensions",
        "approval-gate",
        "laptop-diagnostic",
        "resilience",
        "scheduling",
    }

    copied = tmp_path / "showcases"
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", copied)
    shutil.copytree(
        copied / "packages/approval-gate",
        copied / "packages/injected-sixth",
    )
    catalog_path = copied / "catalog.yaml"
    raw_catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    injected = dict(
        next(
            scenario
            for scenario in raw_catalog["scenarios"]
            if scenario["id"] == "approval-gate"
        )
    )
    injected.update(
        {
            "id": "injected-sixth",
            "display_name": "Injected Sixth Tour",
            "workflow_path": (
                "packages/injected-sixth/workflows/approval-gate.yaml"
            ),
        }
    )
    raw_catalog["scenarios"].append(injected)
    catalog_path.write_text(
        yaml.safe_dump(raw_catalog, sort_keys=False), encoding="utf-8"
    )
    package_digests = {
        package.name: showcase_module._tree_digest(package)
        for package in sorted((copied / "packages").iterdir())
    }
    (copied / "digests.json").write_text(
        json.dumps(
            {
                "catalog_sha256": sha256(catalog_path.read_bytes()).hexdigest(),
                "packages": package_digests,
                "schema_version": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    @contextmanager
    def installed_bundle(_explicit=None):
        yield copied

    monkeypatch.setattr(showcase_module, "_bundle_path", installed_bundle)
    restamped_catalog = load_showcase_catalog()
    assert all(
        scenario.verified_bundled_provenance
        for scenario in restamped_catalog.values()
    )

    # These are the three legacy subset/membership checks. All accept the
    # fully authenticated sixth scenario.
    assert {
        "ai-extensions",
        "laptop-diagnostic",
        "resilience",
        "scheduling",
    } <= set(restamped_catalog)
    assert {
        "approval-gate",
        "laptop-diagnostic",
        "resilience",
        "scheduling",
    } <= set(restamped_catalog)
    assert "approval-gate" in restamped_catalog
    assert all(item.package_digest for item in restamped_catalog.values())

    with pytest.raises(AssertionError):
        assert set(restamped_catalog) == expected_ids


def test_bundled_approval_gate_is_verified_parameterless_and_portable() -> None:
    catalog = load_showcase_catalog()
    scenario = catalog["approval-gate"]
    package = showcase_module._scenario_package(scenario)
    preflight = preflight_showcase("approval-gate", hermes_home=REPO_ROOT)

    assert scenario.verified_bundled_provenance is True
    assert scenario.requires_ai is False
    assert scenario.requires_network is False
    assert "operator-approval" in scenario.capability_claims
    assert [node.node_type for node in package.definition.nodes] == ["approval"]
    assert preflight["input_requirements"] == []


def test_explicit_catalog_copy_is_not_authenticated_as_bundled_distribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied = tmp_path / "showcases"
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", copied)

    catalog = load_showcase_catalog(copied)

    assert all(not item.verified_bundled_provenance for item in catalog.values())
    monkeypatch.setattr(showcase_module, "load_showcase_catalog", lambda: catalog)
    with pytest.raises(ShowcaseCatalogError, match="bundled distribution provenance"):
        showcase_module.run_showcase(
            "laptop-diagnostic",
            hermes_home=tmp_path / "profile",
            symptom="fictional slow startup",
        )


def test_verified_loader_is_rootless_bounded_and_cache_invalidates_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied = tmp_path / "showcases"
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", copied)

    @contextmanager
    def installed_bundle(_explicit=None):
        yield copied

    monkeypatch.setattr(showcase_module, "_bundle_path", installed_bundle)
    showcase_module._clear_verified_showcase_cache_for_tests()
    digest_calls = 0
    original_tree_digest = showcase_module._tree_digest

    def counted_tree_digest(*args, **kwargs):
        nonlocal digest_calls
        digest_calls += 1
        return original_tree_digest(*args, **kwargs)

    monkeypatch.setattr(showcase_module, "_tree_digest", counted_tree_digest)
    first_budget = WorkflowResourceReadBudget(
        max_file_bytes=1024 * 1024,
        max_total_bytes=8 * 1024 * 1024,
        max_files=512,
    )
    first = showcase_module.load_verified_showcase_packages(
        read_budget=first_budget
    )
    first_digest_calls = digest_calls
    second = showcase_module.load_verified_showcase_packages(
        read_budget=WorkflowResourceReadBudget(
            max_file_bytes=1024 * 1024,
            max_total_bytes=8 * 1024 * 1024,
            max_files=512,
        )
    )

    assert first["approval-gate"].scenario.id == "approval-gate"
    assert second["approval-gate"].bundle_digest == first["approval-gate"].bundle_digest
    assert first_digest_calls > 0
    assert digest_calls == first_digest_calls

    approval = copied / "packages/approval-gate/workflows/approval-gate.yaml"
    approval.write_text(approval.read_text() + "\n", encoding="utf-8")
    with pytest.raises(ShowcaseCatalogError, match="package digest mismatch"):
        showcase_module.load_verified_showcase_packages(
            read_budget=WorkflowResourceReadBudget(
                max_file_bytes=1024 * 1024,
                max_total_bytes=8 * 1024 * 1024,
                max_files=512,
            )
        )
    assert digest_calls > first_digest_calls


def test_verified_loader_coalesces_concurrent_cache_misses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied = tmp_path / "showcases"
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", copied)

    @contextmanager
    def installed_bundle(_explicit=None):
        yield copied

    monkeypatch.setattr(showcase_module, "_bundle_path", installed_bundle)
    original_tree_digest = showcase_module._tree_digest
    digest_calls = 0

    def counted_tree_digest(*args, **kwargs):
        nonlocal digest_calls
        digest_calls += 1
        time.sleep(0.005)
        return original_tree_digest(*args, **kwargs)

    monkeypatch.setattr(showcase_module, "_tree_digest", counted_tree_digest)
    showcase_module._clear_verified_showcase_cache_for_tests()
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda _index: showcase_module.load_verified_showcase_packages(
                    read_budget=WorkflowResourceReadBudget(
                        max_file_bytes=1024 * 1024,
                        max_total_bytes=8 * 1024 * 1024,
                        max_files=512,
                    )
                ),
                range(4),
            )
        )
    concurrent_calls = digest_calls

    showcase_module._clear_verified_showcase_cache_for_tests()
    digest_calls = 0
    showcase_module.load_verified_showcase_packages(
        read_budget=WorkflowResourceReadBudget(
            max_file_bytes=1024 * 1024,
            max_total_bytes=8 * 1024 * 1024,
            max_files=512,
        )
    )

    assert all("approval-gate" in result for result in results)
    assert concurrent_calls == digest_calls


def test_verified_loader_warm_hits_verify_outside_cache_lock_and_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied = tmp_path / "showcases"
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", copied)

    @contextmanager
    def installed_bundle(_explicit=None):
        yield copied

    def budget() -> WorkflowResourceReadBudget:
        return WorkflowResourceReadBudget(
            max_file_bytes=1024 * 1024,
            max_total_bytes=8 * 1024 * 1024,
            max_files=512,
        )

    monkeypatch.setattr(showcase_module, "_bundle_path", installed_bundle)
    original_cache_lock = showcase_module._VERIFIED_SHOWCASE_CACHE_LOCK

    class CacheLockProbe:
        def __init__(self) -> None:
            self._lock = original_cache_lock
            self._owner = threading.local()

        def __enter__(self):
            self._lock.acquire()
            self._owner.held = True
            return self

        def __exit__(self, *_exc_info) -> None:
            self._owner.held = False
            self._lock.release()

        def locked(self) -> bool:
            return self._lock.locked()

        def held_by_current_thread(self) -> bool:
            return getattr(self._owner, "held", False)

    cache_lock_probe = CacheLockProbe()
    monkeypatch.setattr(
        showcase_module,
        "_VERIFIED_SHOWCASE_CACHE_LOCK",
        cache_lock_probe,
    )
    showcase_module._clear_verified_showcase_cache_for_tests()
    showcase_module.load_verified_showcase_packages(read_budget=budget())

    original_bundle_digest = showcase_module._bundle_digest
    original_tree_signature = showcase_module._bundle_tree_signature
    observations: list[tuple[str, bool]] = []
    active_signatures = 0
    active_lock = threading.Lock()
    signatures_overlap = threading.Event()
    release_signatures = threading.Event()

    def probed_bundle_digest(*args, **kwargs):
        observations.append(
            ("bundle_digest", cache_lock_probe.held_by_current_thread())
        )
        return original_bundle_digest(*args, **kwargs)

    def probed_tree_signature(*args, **kwargs):
        nonlocal active_signatures
        observations.append(
            ("tree_signature", cache_lock_probe.held_by_current_thread())
        )
        with active_lock:
            active_signatures += 1
            if active_signatures == 2:
                signatures_overlap.set()
        try:
            release_signatures.wait(timeout=2)
            return original_tree_signature(*args, **kwargs)
        finally:
            with active_lock:
                active_signatures -= 1

    monkeypatch.setattr(showcase_module, "_bundle_digest", probed_bundle_digest)
    monkeypatch.setattr(showcase_module, "_bundle_tree_signature", probed_tree_signature)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                showcase_module.load_verified_showcase_packages,
                read_budget=budget(),
            )
            for _index in range(2)
        ]
        overlapped = signatures_overlap.wait(timeout=2)
        release_signatures.set()
        results = [future.result(timeout=5) for future in futures]

    assert all("approval-gate" in result for result in results)
    assert overlapped, "warm-hit tree verification serialized behind the cache lock"
    assert observations
    assert not [name for name, lock_held in observations if lock_held]


def test_verified_loader_generation_change_prevents_stale_warm_hit_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied = tmp_path / "showcases"
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", copied)

    @contextmanager
    def installed_bundle(_explicit=None):
        yield copied

    def budget() -> WorkflowResourceReadBudget:
        return WorkflowResourceReadBudget(
            max_file_bytes=1024 * 1024,
            max_total_bytes=8 * 1024 * 1024,
            max_files=512,
        )

    monkeypatch.setattr(showcase_module, "_bundle_path", installed_bundle)
    showcase_module._clear_verified_showcase_cache_for_tests()
    initial = showcase_module.load_verified_showcase_packages(read_budget=budget())
    initial_package = initial["approval-gate"]

    original_tree_signature = showcase_module._bundle_tree_signature
    pause_hit = threading.local()
    hit_signature_ready = threading.Event()
    release_hit = threading.Event()

    def pausing_tree_signature(*args, **kwargs):
        signature = original_tree_signature(*args, **kwargs)
        if getattr(pause_hit, "enabled", False):
            hit_signature_ready.set()
            release_hit.wait(timeout=5)
        return signature

    def paused_warm_hit():
        pause_hit.enabled = True
        return showcase_module.load_verified_showcase_packages(read_budget=budget())

    monkeypatch.setattr(
        showcase_module,
        "_bundle_tree_signature",
        pausing_tree_signature,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        stale_candidate = executor.submit(paused_warm_hit)
        assert hit_signature_ready.wait(timeout=2)
        forced_future = executor.submit(
            showcase_module.load_verified_showcase_packages,
            read_budget=budget(),
            force_reverify=True,
        )
        force_interleaved = False
        try:
            forced = forced_future.result(timeout=2)
            force_interleaved = True
        except TimeoutError:
            forced = None
        finally:
            release_hit.set()
        stale_checked = stale_candidate.result(timeout=5)
        if forced is None:
            forced = forced_future.result(timeout=5)

    assert force_interleaved, "force reverify could not advance the cache generation"
    assert forced["approval-gate"] is not initial_package
    assert stale_checked["approval-gate"] is forced["approval-gate"]


def test_verified_loader_parses_only_digest_authenticated_package_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied = tmp_path / "showcases"
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", copied)

    @contextmanager
    def installed_bundle(_explicit=None):
        yield copied

    original_load_workflow = showcase_module.load_workflow

    def transient_unverified_parse(path, **kwargs):
        workflow = Path(path)
        if workflow.name != "approval-gate.yaml":
            return original_load_workflow(workflow, **kwargs)
        authenticated = workflow.read_bytes()
        sidecar = workflow.with_name("approval-gate.hermes.yaml")
        authenticated_sidecar = sidecar.read_bytes()
        workflow.write_text(
            "name: approval-gate\n"
            "description: TRANSIENT UNVERIFIED\n"
            "nodes:\n"
            "  - id: operator-approval\n"
            "    approval:\n"
            "      message: changed\n",
            encoding="utf-8",
        )
        sidecar.write_text(
            "overlap_policy: queue\n"
            "execution_environment: trusted_local\n"
            "outward_action_nodes: [operator-approval]\n",
            encoding="utf-8",
        )
        try:
            return original_load_workflow(workflow, **kwargs)
        finally:
            workflow.write_bytes(authenticated)
            sidecar.write_bytes(authenticated_sidecar)

    monkeypatch.setattr(showcase_module, "_bundle_path", installed_bundle)
    monkeypatch.setattr(showcase_module, "load_workflow", transient_unverified_parse)
    monkeypatch.setattr(
        showcase_module,
        "_bundle_tree_signature",
        lambda *_args, **_kwargs: (("stable-platform-signature",),),
    )
    showcase_module._clear_verified_showcase_cache_for_tests()

    verified = showcase_module.load_verified_showcase_packages(
        read_budget=WorkflowResourceReadBudget(
            max_file_bytes=1024 * 1024,
            max_total_bytes=8 * 1024 * 1024,
            max_files=512,
        )
    )

    assert verified["approval-gate"].package.definition.description == (
        "Pause for explicit operator approval before completing the bundled tour"
    )
    assert verified["approval-gate"].package.sidecar["outward_action_nodes"] == ()


def test_no_budget_catalog_parses_digest_authenticated_package_bytes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied = tmp_path / "showcases"
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", copied)
    workflow = copied / "packages/approval-gate/workflows/approval-gate.yaml"
    sidecar = workflow.with_name("approval-gate.hermes.yaml")
    replacement_sidecar = tmp_path / "replacement.hermes.yaml"
    replacement_sidecar.write_text(
        "overlap_policy: queue\n"
        "execution_environment: trusted_local\n"
        "outward_action_nodes: [operator-approval]\n",
        encoding="utf-8",
    )
    original_tree_digest = showcase_module._tree_digest
    parsed_descriptions: list[str] = []
    parsed_outward_action_nodes: list[tuple[str, ...] | None] = []

    def mutate_after_authenticated_digest(root, *args, **kwargs):
        digest = original_tree_digest(root, *args, **kwargs)
        if Path(root).name == "approval-gate":
            workflow.write_text(
                "name: approval-gate\n"
                "description: TRANSIENT UNVERIFIED\n"
                "nodes:\n"
                "  - id: operator-approval\n"
                "    approval:\n"
                "      message: changed\n",
                encoding="utf-8",
            )
            sidecar.unlink()
            sidecar.symlink_to(replacement_sidecar)
        return digest

    original_load_workflow = showcase_module.load_workflow
    original_load_snapshot = showcase_module.load_workflow_snapshot

    def record_disk_parse(path, **kwargs):
        package = original_load_workflow(path, **kwargs)
        if Path(path).name == "approval-gate.yaml":
            parsed_descriptions.append(package.definition.description)
            parsed_outward_action_nodes.append(
                package.sidecar.get("outward_action_nodes")
            )
        return package

    def record_snapshot_parse(path, **kwargs):
        package = original_load_snapshot(path, **kwargs)
        if Path(path).name == "approval-gate.yaml":
            parsed_descriptions.append(package.definition.description)
            parsed_outward_action_nodes.append(
                package.sidecar.get("outward_action_nodes")
            )
        return package

    monkeypatch.setattr(showcase_module, "_tree_digest", mutate_after_authenticated_digest)
    monkeypatch.setattr(showcase_module, "load_workflow", record_disk_parse)
    monkeypatch.setattr(showcase_module, "load_workflow_snapshot", record_snapshot_parse)

    load_showcase_catalog(copied)

    assert parsed_descriptions == [
        "Pause for explicit operator approval before completing the bundled tour"
    ]
    assert parsed_outward_action_nodes == [()]


def test_tree_entry_budget_stops_before_unbounded_rglob_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "package"
    root.mkdir()
    entries = []
    for index in range(10):
        entry = root / f"{index:02d}.txt"
        entry.write_text("x", encoding="utf-8")
        entries.append(entry)
    yielded = 0
    scanned = 0
    original_scandir = showcase_module.os.scandir

    def overlong_rglob(path: Path, _pattern: str):
        nonlocal yielded
        assert path == root
        for entry in entries:
            yielded += 1
            if yielded > 4:
                raise AssertionError("enumerated beyond the bounded prefix")
            yield entry

    class CountingScandir:
        def __init__(self, path):
            self._inner = original_scandir(path)

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *args):
            return self._inner.__exit__(*args)

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal scanned
            child = next(self._inner)
            scanned += 1
            if scanned > 4:
                raise AssertionError("scanned beyond the bounded prefix")
            return child

    monkeypatch.setattr(Path, "rglob", overlong_rglob)
    monkeypatch.setattr(showcase_module.os, "scandir", CountingScandir)
    budget = WorkflowResourceReadBudget(
        max_file_bytes=16,
        max_total_bytes=64,
        max_files=3,
    )

    with pytest.raises(
        WorkflowResourceCapacityError,
        match="showcase package entry limit exceeded",
    ):
        showcase_module._tree_entries(root, budget)

    assert yielded == 0
    assert scanned == 4


@pytest.mark.parametrize("ancestor", ["package", "packages"])
@pytest.mark.parametrize("loader", ["cli", "verified"])
def test_showcase_loaders_reject_symlinked_package_ancestors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ancestor: str,
    loader: str,
) -> None:
    copied = tmp_path / "showcases"
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", copied)
    original = (
        copied / "packages" / "approval-gate"
        if ancestor == "package"
        else copied / "packages"
    )
    target = (
        copied / "packages" / "approval-gate-target"
        if ancestor == "package"
        else copied / "packages-target"
    )
    original.rename(target)
    original.symlink_to(target, target_is_directory=True)

    with pytest.raises(ShowcaseCatalogError, match="symlink"):
        if loader == "cli":
            load_showcase_catalog(copied)
        else:
            @contextmanager
            def installed_bundle(_explicit=None):
                yield copied

            monkeypatch.setattr(showcase_module, "_bundle_path", installed_bundle)
            showcase_module._clear_verified_showcase_cache_for_tests()
            showcase_module.load_verified_showcase_packages(
                read_budget=WorkflowResourceReadBudget(
                    max_file_bytes=1024 * 1024,
                    max_total_bytes=8 * 1024 * 1024,
                    max_files=512,
                )
            )


def test_bounded_catalog_refuses_oversized_tampering(tmp_path: Path) -> None:
    copied = tmp_path / "showcases"
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", copied)
    (copied / "packages/approval-gate/oversized.txt").write_bytes(b"x" * 4097)
    budget = WorkflowResourceReadBudget(
        max_file_bytes=4096,
        max_total_bytes=16384,
        max_files=128,
    )

    with pytest.raises(WorkflowResourceCapacityError):
        load_showcase_catalog(copied, read_budget=budget, allow_repair=False)


def test_http_style_catalog_verification_never_repairs_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied = tmp_path / "showcases"
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", copied)
    catalog_path = copied / "catalog.yaml"
    catalog_path.write_text(catalog_path.read_text() + "tampered: true\n")

    @contextmanager
    def installed_bundle(_explicit=None):
        yield copied

    monkeypatch.setattr(showcase_module, "_bundle_path", installed_bundle)
    monkeypatch.setattr(
        capability_staging,
        "repair_authenticated_resource_checkout",
        lambda _path: (_ for _ in ()).throw(AssertionError("repair attempted")),
    )

    with pytest.raises(ShowcaseCatalogError, match="catalog digest mismatch"):
        load_showcase_catalog(allow_repair=False)


def test_cli_run_still_rejects_isolated_backend_showcase_without_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied = tmp_path / "showcases"
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", copied)
    sidecar = (
        copied
        / "packages"
        / "approval-gate"
        / "workflows"
        / "approval-gate.hermes.yaml"
    )
    sidecar.write_text(
        sidecar.read_text(encoding="utf-8").replace(
            "execution_environment: trusted_local",
            "execution_environment: isolated_backend_required",
        ),
        encoding="utf-8",
    )
    manifest_path = copied / "digests.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["packages"]["approval-gate"] = showcase_module._tree_digest(
        copied / "packages" / "approval-gate"
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    @contextmanager
    def installed_bundle(_explicit=None):
        yield copied

    monkeypatch.setattr(showcase_module, "_bundle_path", installed_bundle)

    with pytest.raises(
        ShowcaseCatalogError,
        match="failed ordinary execution-risk policy",
    ):
        showcase_module.run_showcase(
            "approval-gate",
            hermes_home=tmp_path / "home",
        )


@pytest.mark.parametrize(
    ("showcase_id", "eligible"),
    [
        ("approval-gate", True),
        ("resilience", True),
        ("laptop-diagnostic", True),
        ("ai-extensions", False),
        ("scheduling", False),
    ],
)
def test_background_api_eligibility_is_derived_from_verified_scenario_policy(
    showcase_id: str, eligible: bool
) -> None:
    scenario = load_showcase_catalog()[showcase_id]

    assert showcase_module.showcase_background_api_eligible(scenario) is eligible


def test_default_catalog_repairs_crlf_only_managed_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    bundle = checkout / "plugins/workflow/showcases"
    bundle.parent.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / ".gitattributes", checkout / ".gitattributes")
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", bundle)
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.name", "Showcase Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "commit", "-qm", "fixture"], check=True
    )
    for path in bundle.rglob("*"):
        if path.is_file():
            data = path.read_bytes().replace(b"\r\n", b"\n")
            path.write_bytes(data.replace(b"\n", b"\r\n"))

    @contextmanager
    def installed_bundle(_explicit=None):
        yield bundle

    monkeypatch.setattr(showcase_module, "_bundle_path", installed_bundle)

    catalog = showcase_module.load_showcase_catalog()

    assert "laptop-diagnostic" in catalog
    assert b"\r\n" not in (bundle / "catalog.yaml").read_bytes()


def test_repair_materializes_elsewhere_when_in_place_git_restores_are_noops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    bundle = checkout / "plugins/workflow/showcases"
    bundle.parent.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / ".gitattributes", checkout / ".gitattributes")
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", bundle)
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.name", "Showcase Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "commit", "-qm", "fixture"], check=True
    )
    for path in bundle.rglob("*"):
        if path.is_file():
            data = path.read_bytes().replace(b"\r\n", b"\n")
            path.write_bytes(data.replace(b"\n", b"\r\n"))

    original_run = capability_staging.subprocess.run

    def successful_noop_in_place_restore(command, *args, **kwargs):
        if command[-4:] == ["checkout", "HEAD", "--", "plugins/workflow/showcases"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if "checkout-index" in command and not any(
            item.startswith("--prefix=") for item in command
        ):
            return subprocess.CompletedProcess(command, 0, "", "")
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(
        capability_staging.subprocess, "run", successful_noop_in_place_restore
    )

    assert capability_staging.repair_authenticated_resource_checkout(bundle) is True
    assert b"\r\n" not in (bundle / "catalog.yaml").read_bytes()


def test_default_catalog_does_not_repair_semantic_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    bundle = checkout / "plugins/workflow/showcases"
    bundle.parent.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / ".gitattributes", checkout / ".gitattributes")
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", bundle)
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.name", "Showcase Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "commit", "-qm", "fixture"], check=True
    )
    catalog_path = bundle / "catalog.yaml"
    data = catalog_path.read_bytes().replace(b"\r\n", b"\n")
    catalog_path.write_bytes(data.replace(b"\n", b"\r\n") + b"tampered: true\r\n")

    @contextmanager
    def installed_bundle(_explicit=None):
        yield bundle

    monkeypatch.setattr(showcase_module, "_bundle_path", installed_bundle)

    with pytest.raises(ShowcaseCatalogError, match="catalog digest mismatch"):
        showcase_module.load_showcase_catalog()

    assert catalog_path.read_bytes().endswith(b"tampered: true\r\n")


def test_catalog_validation_rejects_traversal_and_destructive_claims(
    tmp_path: Path,
) -> None:
    root = tmp_path / "showcases"
    root.mkdir()
    (root / "catalog.yaml").write_text(
        "schema_version: 1\nscenarios:\n"
        "  - id: bad\n"
        "    display_name: Bad\n"
        "    purpose: bad\n"
        "    bundle_version: '1'\n"
        "    package_version: '1'\n"
        "    workflow_path: ../escape.yaml\n"
        "    interaction_mode: guided\n"
        "    offline: true\n"
        "    requires_ai: false\n"
        "    requires_network: false\n"
        "    safety_class: destructive\n"
        "    supported_platforms: [linux]\n"
        "    expected_checkpoints: []\n"
        "    expected_terminal_outcomes: [succeeded]\n"
        "    expected_artifacts: []\n"
        "    capability_claims: [corruption]\n"
        "    limits: {wall_seconds: 1}\n"
        "    cleanup_ownership: bad\n",
        encoding="utf-8",
    )
    (root / "digests.json").write_text(
        json.dumps({"schema_version": 1, "packages": {"bad": "0" * 64}}),
        encoding="utf-8",
    )

    with pytest.raises(ShowcaseCatalogError):
        load_showcase_catalog(root)


def test_preflight_is_side_effect_free_and_reports_explicit_opt_ins(
    tmp_path: Path, monkeypatch
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("preflight initialized execution or network state")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    laptop = preflight_showcase("laptop-diagnostic", hermes_home=tmp_path)
    ai = preflight_showcase("ai-extensions", hermes_home=tmp_path)

    assert laptop["runnable"] is True
    assert laptop["offline"] is True
    assert laptop["requires_confirmation"] is False
    assert laptop["input_requirements"] == [
        {"name": "evidence", "kind": "file", "required": True, "max_bytes": 65536},
        {"name": "symptom", "kind": "text", "required": True, "max_bytes": 4096},
    ]
    assert ai["requires_confirmation"] is True
    assert ai["confirmation_kind"] == "ai"
    assert ai["confirmation_token"]


def test_showcase_cli_list_and_missing_input_have_stable_exit_categories(
    tmp_path: Path, capsys
) -> None:
    parser = argparse.ArgumentParser()
    command = parser.add_subparsers().add_parser("workflow")
    register_cli(command)

    listed = parser.parse_args(
        ["workflow", "--hermes-home", str(tmp_path), "showcase", "list", "--json"]
    )
    assert listed.func(listed) == 0
    listed_envelope = json.loads(capsys.readouterr().out)
    assert listed_envelope["ok"] is True
    assert {
        "ai-extensions",
        "laptop-diagnostic",
        "resilience",
        "scheduling",
    } <= {item["id"] for item in listed_envelope["result"]}

    missing = parser.parse_args(
        [
            "workflow", "--hermes-home", str(tmp_path), "showcase", "run",
            "laptop-diagnostic", "--json", "--idempotency-key", "missing-input",
        ]
    )
    assert missing.func(missing) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "showcase_input_required"
    assert payload["result"]["reason_code"] == "showcase_input_required"


@pytest.mark.parametrize(
    ("machine_flag", "json_output"),
    [("--json", True), ("--no-wait", False)],
)
def test_showcase_machine_start_requires_caller_idempotency_key(
    tmp_path: Path, capsys, machine_flag: str, json_output: bool
) -> None:
    parser = argparse.ArgumentParser()
    command = parser.add_subparsers().add_parser("workflow")
    register_cli(command)
    args = parser.parse_args(
        [
            "workflow",
            "--hermes-home",
            str(tmp_path),
            "showcase",
            "run",
            "laptop-diagnostic",
            machine_flag,
        ]
    )

    assert args.func(args) == 2
    captured = capsys.readouterr()
    if json_output:
        assert json.loads(captured.out)["error"]["code"] == "idempotency_key_required"
    else:
        assert "--idempotency-key is required" in captured.err
