from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from plugins.workflow.api_admission import (
    ApiAdmissionAuthority,
    ApiAdmissionError,
    start_api_run,
)
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.models import WorkflowValidationError
from plugins.workflow.schema import load_workflow, load_workflow_snapshot
from plugins.workflow.store import RunStore
from plugins.workflow.trust import (
    WorkflowResourceReadBudget,
    WorkflowTrustError,
    WorkflowTrustStore,
    build_risk_summary,
    compute_package_digest,
    preflight_execution,
)


def _package(workflow_writer, root):
    (root / "commands").mkdir(parents=True)
    (root / "commands" / "audit.md").write_text(
        "---\nargument-hint: <target>\n---\nAudit $ARGUMENTS\n", encoding="utf-8"
    )
    (root / "scripts").mkdir()
    (root / "scripts" / "helper.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "mcp").mkdir()
    (root / "mcp" / "echo.yaml").write_text(
        "command: python\n"
        "args: [servers/echo.py, --config=config/settings.json]\n"
        "runtime_files: [data/value.txt]\n"
        "env: {TOKEN: '${TOKEN}'}\n",
        encoding="utf-8",
    )
    (root / "servers").mkdir()
    (root / "servers" / "echo.py").write_text("print('echo')\n", encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "settings.json").write_text("{}\n", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "value.txt").write_text("sealed\n", encoding="utf-8")
    path = workflow_writer(
        root / "workflows",
        name="risky",
        filename="risky.yaml",
        nodes=[
            {
                "id": "command",
                "command": "audit",
                "allowed_tools": ["Read"],
                "skills": ["review"],
            },
            {"id": "shell", "bash": "printf secret-body", "depends_on": ["command"]},
            {
                "id": "script",
                "script": "helper",
                "runtime": "uv",
                "depends_on": ["shell"],
            },
            {
                "id": "agent",
                "prompt": "SECRET_PROMPT",
                "mcp": "mcp/echo.yaml",
                "provider": "claude",
                "depends_on": ["script"],
            },
        ],
    )
    path.with_name("risky.hermes.yaml").write_text(
        "outward_action_nodes: [agent]\nrequired_secrets: [API_TOKEN]\nexecution_environment: isolated_backend_required\n",
        encoding="utf-8",
    )
    return load_workflow(path)


def test_archon_normalizer_upgrade_changes_risk_identity_without_source_change(
    tmp_path, workflow_writer
) -> None:
    path = workflow_writer(tmp_path / "risk-upgrade")
    sidecar = path.with_name(f"{path.stem}.hermes.yaml")
    sidecar.write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    v3 = load_workflow(path)
    v2 = load_workflow_snapshot(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=sidecar.read_bytes(),
        normalizer_version=2,
    )

    v3_summary = build_risk_summary(v3, assess_compatibility(v3))
    v2_summary = build_risk_summary(v2, assess_compatibility(v2))

    assert compute_package_digest(v3) == compute_package_digest(v2)
    assert v3_summary.risk_digest != v2_summary.risk_digest


def _phase2_risk_digest(compatibility, summary) -> str:
    document = {
        "package_digest": summary.package_digest,
        "shell_or_script_nodes": summary.shell_or_script_nodes,
        "requested_tools": summary.requested_tools,
        "requested_skills": summary.requested_skills,
        "local_mcp_servers": summary.local_mcp_servers,
        "providers": summary.providers,
        "outward_action_nodes": summary.outward_action_nodes,
        "required_secret_names": summary.required_secret_names,
        "execution_environment": summary.execution_environment,
        "compatibility": compatibility.level.value,
        "blocking_findings": tuple(
            finding.path for finding in compatibility.blocking_findings
        ),
    }
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@pytest.mark.parametrize("version", [1, 2])
def test_phase2_archon_risk_digest_remains_exact(
    tmp_path, workflow_writer, version
) -> None:
    path = workflow_writer(tmp_path / f"archon-v{version}")
    sidecar = path.with_name(f"{path.stem}.hermes.yaml")
    sidecar.write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow_snapshot(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=sidecar.read_bytes(),
        normalizer_version=version,
    )
    compatibility = assess_compatibility(package)
    summary = build_risk_summary(package, compatibility)

    assert summary.risk_digest == _phase2_risk_digest(compatibility, summary)


def test_phase2_legacy_risk_digest_fixture_remains_exact() -> None:
    path = (
        Path(__file__).parent
        / "fixtures"
        / "portable"
        / "workflows"
        / "minimal.yaml"
    )
    package = load_workflow(path)

    assert package.language.normalizer_version == 2
    assert build_risk_summary(
        package, assess_compatibility(package)
    ).risk_digest == "355856bdcfd7a05272773f82309d566a6b83dfb7c22046d52277465d6afa84f9"


def _resource_boundary_package(
    workflow_writer,
    home,
    *,
    canonical_file_count: int,
):
    command_root = home / "commands"
    command_root.mkdir(parents=True)
    nodes = []
    # The package authority includes the workflow definition. Every remaining
    # canonical file is an empty, explicitly declared command resource.
    for index in range(canonical_file_count - 1):
        name = f"empty-{index:03d}"
        (command_root / f"{name}.md").write_bytes(b"")
        nodes.append({"id": f"command-{index:03d}", "command": name})
    path = workflow_writer(
        home / "workflows",
        name=f"resource-boundary-{canonical_file_count}",
        filename=f"resource-boundary-{canonical_file_count}.yaml",
        nodes=nodes,
    )
    return load_workflow(path)


def _trust_for_api_admission(home, package) -> None:
    compatibility = assess_compatibility(package)
    digest = compute_package_digest(package)
    risk = build_risk_summary(package, compatibility)
    WorkflowTrustStore(home).trust(
        digest.sha256,
        actor="resource-boundary-test",
        risk_digest=risk.risk_digest,
    )


def _healthy_coordinator(store: RunStore) -> None:
    acquired = CoordinatorStore(store.database).try_acquire(
        CoordinatorIdentity(
            owner_id="resource-boundary-test",
            host_kind="web",
            host_instance_id="resource-boundary-test",
            pid=1,
            process_start_time=None,
        ),
        now=datetime.now(timezone.utc),
        lease_seconds=60,
    )
    assert acquired.is_leader


def _api_authority() -> ApiAdmissionAuthority:
    return ApiAdmissionAuthority(
        principal="resource-boundary-test",
        namespace="resource-boundary-test",
        operator_scope=None,
        source_instance="desktop:resource-boundary-test",
        assurance="local_admin_claim",
        trigger_source="desktop",
    )


def test_digest_covers_yaml_sidecar_and_executable_resources(workflow_writer, tmp_path):
    package = _package(workflow_writer, tmp_path / "package")
    original = compute_package_digest(package)

    assert original.covered_relative_paths == (
        "commands/audit.md",
        "config/settings.json",
        "data/value.txt",
        "mcp/echo.yaml",
        "scripts/helper.py",
        "servers/echo.py",
        "workflows/risky.hermes.yaml",
        "workflows/risky.yaml",
    )
    for relative_path in original.covered_relative_paths:
        clone_root = tmp_path / f"clone-{relative_path.replace('/', '-')}"
        shutil.copytree(package.root, clone_root)
        clone = load_workflow(clone_root / "workflows" / "risky.yaml")
        target = clone_root / relative_path
        target.write_bytes(target.read_bytes() + b"# mutation\n")
        assert compute_package_digest(clone).sha256 != original.sha256


def test_production_authority_accepts_512_files_and_refuses_513_before_admission(
    workflow_writer, tmp_path, monkeypatch
):
    accepted_home = tmp_path / "accepted"
    accepted_package = _resource_boundary_package(
        workflow_writer,
        accepted_home,
        canonical_file_count=512,
    )
    _trust_for_api_admission(accepted_home, accepted_package)
    accepted_store = RunStore(accepted_home)
    _healthy_coordinator(accepted_store)

    accepted = start_api_run(
        accepted_store,
        hermes_home=accepted_home,
        workdir=tmp_path,
        user_home=tmp_path,
        workflow_name=accepted_package.definition.name,
        values={},
        idempotency_key="accept-512-files",
        concurrency_policy="queue",
        authority=_api_authority(),
        catalog_source="profile",
    )
    accepted_run_id = str(accepted["run_id"])

    assert accepted_store.run_directory(accepted_run_id).is_dir()
    assert accepted_store.get_run_status(accepted_run_id)["status"] in {
        "queued",
        "running",
    }

    refused_home = tmp_path / "refused"
    refused_package = _resource_boundary_package(
        workflow_writer,
        refused_home,
        canonical_file_count=513,
    )
    _trust_for_api_admission(refused_home, refused_package)
    refused_store = RunStore(refused_home)
    _healthy_coordinator(refused_store)
    before_runs = refused_store.list_runs()
    before_events = refused_store.list_admission_events()
    before_run_entries = tuple(refused_store.runs_root.iterdir())
    before_staging_entries = tuple(refused_store.staging_root.iterdir())
    preparation_calls = []

    def forbidden_prepare(*_args, **_kwargs):
        preparation_calls.append(True)
        raise AssertionError(
            "513-file authority reached forbidden snapshot preparation"
        )

    monkeypatch.setattr(
        refused_store,
        "prepare_run_snapshot",
        forbidden_prepare,
    )

    with pytest.raises(ApiAdmissionError) as exc_info:
        start_api_run(
            refused_store,
            hermes_home=refused_home,
            workdir=tmp_path,
            user_home=tmp_path,
            workflow_name=refused_package.definition.name,
            values={},
            idempotency_key="refuse-513-files",
            concurrency_policy="queue",
            authority=_api_authority(),
            catalog_source="profile",
        )

    assert exc_info.value.code == "workflow_catalog_capacity"
    assert exc_info.value.status_code == 503
    assert exc_info.value.retryable is True
    assert preparation_calls == []
    assert refused_store.list_runs() == before_runs == ()
    assert refused_store.list_admission_events() == before_events == ()
    assert tuple(refused_store.runs_root.iterdir()) == before_run_entries == ()
    assert tuple(refused_store.staging_root.iterdir()) == before_staging_entries == ()


@pytest.mark.parametrize("mutation", ["delete", "rename", "symlink"])
def test_cached_mcp_transitive_digest_survives_mutable_path_races(
    workflow_writer, tmp_path, mutation
):
    package = _package(workflow_writer, tmp_path / "package")
    covered = compute_package_digest(package).covered_relative_paths
    contents = {
        relative: (package.root / relative).read_bytes() for relative in covered
    }
    authority = WorkflowResourceReadBudget.from_authenticated(package.root, contents)
    expected = compute_package_digest(package, read_budget=authority)
    target = package.root / "config/settings.json"
    if mutation == "delete":
        target.unlink()
    elif mutation == "rename":
        target.rename(target.with_suffix(".gone"))
    else:
        target.unlink()
        target.symlink_to(tmp_path / "outside.json")

    observed = compute_package_digest(package, read_budget=authority)

    assert observed == expected
    assert "config/settings.json" in observed.covered_relative_paths


@pytest.mark.parametrize("mutation", ["delete", "rename", "replace", "symlink"])
def test_mutable_boundary_verification_still_detects_cached_resource_races(
    workflow_writer, tmp_path, mutation
):
    package = _package(workflow_writer, tmp_path / "package")
    target = package.root / "config/settings.json"
    authority = WorkflowResourceReadBudget(
        max_file_bytes=1024 * 1024,
        max_total_bytes=8 * 1024 * 1024,
        max_files=512,
    )
    authority.read(target)
    if mutation == "delete":
        target.unlink()
    elif mutation == "rename":
        target.rename(target.with_suffix(".gone"))
    elif mutation == "replace":
        target.write_bytes(b"[]\n")
    else:
        outside = tmp_path / "outside.json"
        outside.write_bytes(b"{}\n")
        target.unlink()
        target.symlink_to(outside)

    with pytest.raises(OSError):
        authority.read(target, verify_cached_identity=True)


def test_moving_identical_package_preserves_digest_but_not_profile_trust(
    workflow_writer, tmp_path
):
    package = _package(workflow_writer, tmp_path / "one")
    digest = compute_package_digest(package)
    shutil.copytree(package.root, tmp_path / "two")
    moved = load_workflow(tmp_path / "two" / "workflows" / "risky.yaml")

    assert compute_package_digest(moved) == digest
    store_a = WorkflowTrustStore(tmp_path / "profile-a")
    store_b = WorkflowTrustStore(tmp_path / "profile-b")
    store_a.trust(digest.sha256, actor="user", risk_digest="b" * 64)
    assert store_a.check(digest.sha256) == "trusted"
    assert store_a.check(digest.sha256, risk_digest="c" * 64) == "untrusted"
    assert store_b.check(digest.sha256) == "untrusted"


def test_trust_store_is_atomic_restrictive_and_contains_no_secrets(tmp_path):
    store = WorkflowTrustStore(tmp_path / "profile")
    store.trust("a" * 64, actor="user", risk_digest="b" * 64)

    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["records"]["a" * 64]["actor"] == "user"
    assert "secret" not in store.path.read_text(encoding="utf-8").lower()
    assert store.revoke("a" * 64) is True
    assert store.revoke("a" * 64) is False


def test_trust_store_rejects_non_hex_digests(tmp_path):
    store = WorkflowTrustStore(tmp_path / "profile")
    with pytest.raises(WorkflowTrustError, match="SHA-256"):
        store.trust("z" * 64, actor="user", risk_digest="b" * 64)


def test_corrupt_store_fails_closed_and_concurrent_updates_remain_valid(tmp_path):
    store = WorkflowTrustStore(tmp_path / "profile")
    store.path.parent.mkdir(parents=True)
    store.path.write_text("not json", encoding="utf-8")
    assert store.check("a" * 64) == "untrusted"
    with pytest.raises(WorkflowTrustError, match="corrupt"):
        store.trust("a" * 64, actor="user", risk_digest="b" * 64)

    store.path.unlink()
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda index: store.trust(
                    f"{index:064x}", actor="user", risk_digest=f"{index + 1:064x}"
                ),
                range(16),
            )
        )
    assert len(json.loads(store.path.read_text(encoding="utf-8"))["records"]) == 16


def test_digest_rejects_symlink_escape(workflow_writer, tmp_path):
    package = _package(workflow_writer, tmp_path / "package")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    command = package.root / "commands" / "audit.md"
    command.unlink()
    command.symlink_to(outside)

    with pytest.raises(WorkflowValidationError, match="escape"):
        compute_package_digest(package)


def test_digest_fails_closed_on_unreadable_resource(
    workflow_writer, tmp_path, monkeypatch
):
    package = _package(workflow_writer, tmp_path / "package")
    original_read_bytes = type(package.workflow_path).read_bytes

    def fail_command(path):
        if path.name == "audit.md":
            raise PermissionError("denied")
        return original_read_bytes(path)

    monkeypatch.setattr(type(package.workflow_path), "read_bytes", fail_command)
    with pytest.raises(WorkflowValidationError, match="unreadable"):
        compute_package_digest(package)


def test_inline_script_metacharacters_do_not_trigger_named_resource_lookup(
    workflow_writer, tmp_path
):
    path = workflow_writer(
        tmp_path,
        nodes=[{"id": "inline", "script": "console.log(1)", "runtime": "bun"}],
    )

    digest = compute_package_digest(load_workflow(path))

    assert digest.covered_relative_paths == ("example.yaml",)


def test_trusted_package_defaults_to_local_but_untrusted_package_requires_isolation(
    workflow_writer, tmp_path
):
    package = load_workflow(
        workflow_writer(tmp_path, nodes=[{"id": "safe", "bash": "true"}])
    )
    summary = build_risk_summary(package, assess_compatibility(package))

    assert preflight_execution(summary, trusted=True).mode == "trusted_local"
    with pytest.raises(WorkflowTrustError, match="isolated backend"):
        preflight_execution(summary, trusted=False)


def test_risk_summary_is_redacted_and_untrusted_local_execution_fails_closed(
    workflow_writer, tmp_path
):
    package = _package(workflow_writer, tmp_path / "package")
    compatibility = assess_compatibility(
        package,
        available_tools={"read_file"},
        provider_capabilities={"claude": set()},
        mcp_available=True,
    )
    summary = build_risk_summary(package, compatibility)
    serialized = json.dumps(summary.to_dict(), sort_keys=True)

    assert summary.shell_or_script_nodes == ("shell", "script")
    assert summary.required_secret_names == ("API_TOKEN",)
    assert "SECRET_PROMPT" not in serialized
    assert "secret-body" not in serialized
    with pytest.raises(WorkflowTrustError, match="isolated backend"):
        preflight_execution(summary, trusted=False, backend_capabilities=())
    requirement = preflight_execution(
        summary,
        trusted=False,
        backend_capabilities={
            "process_isolation",
            "package_containment",
            "workdir_containment",
        },
    )
    assert requirement.mode == "isolated_backend_required"
