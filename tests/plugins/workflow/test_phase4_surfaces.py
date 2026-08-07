from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Mapping

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli.plugin_invocation import PluginInvocationContext
from plugins.workflow.actions import WIRE_ACTIONS
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.cli import _doctor_payload, register_cli, show_package
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.evidence import EvidenceReader
from plugins.workflow.gateway_command import workflow_gateway_command
from plugins.workflow.models import LoopSignalConfirmation
from plugins.workflow.notifications import NotificationOutbox
from plugins.workflow.store import ArtifactRef, RunStore
from plugins.workflow.trust import WorkflowPackageDigest


_EXPECTED_SIGNAL_ACTIONS = [
    "status",
    "events",
    "approve",
    "provide-input",
    "cancel",
]
_PRIVATE_PHASE4_FEEDBACK = "PRIVATE_PHASE4_FEEDBACK"
_PRIVATE_PHASE4_PROVIDER_RESPONSE = b"PRIVATE_PHASE4_PROVIDER_RESPONSE\n"


def _api_module():
    path = Path(__file__).parents[3] / "plugins/workflow/dashboard/plugin_api.py"
    name = "workflow_phase4_surfaces_api_test"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _app(router) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def authenticated(request, call_next):
        request.state.local_admin_authenticated = True
        return await call_next(request)

    app.include_router(router, prefix="/api/plugins/workflow")
    return app


def _compile_v4(path: Path):
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
    from plugins.workflow.schema import parse_workflow_source_bytes

    sidecar = b"language_compatibility: archon-2026-07\n"
    path.with_name(f"{path.stem}.hermes.yaml").write_bytes(sidecar)
    source = parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=sidecar,
        source="project",
        precedence=1,
    )
    return compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=4,
    )


def _paused_signal_run(
    tmp_path: Path,
    workflow_writer,
    *,
    operator_scope: str,
) -> tuple[RunStore, str, dict[str, object]]:
    workflow = workflow_writer(
        tmp_path / "signal-source/workflows",
        name="surface-signal",
        filename="surface-signal.yaml",
        interactive=True,
        nodes=[{
            "id": "refine",
            "loop": {
                "prompt": "private phase4 prompt body",
                "until": "DONE",
                "max_iterations": 3,
                "interactive": True,
                "gate_message": "Accept this result or provide feedback",
            },
        }],
    )
    compilation = _compile_v4(workflow)
    store = RunStore(tmp_path / "home", max_executing_runs=20)
    prepared = store.prepare_run_snapshot(
        compilation.package,
        compilation=compilation,
        trusted_package_digest=WorkflowPackageDigest(
            compilation.composite_digest,
            compilation.covered_relative_paths,
        ),
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=compilation.package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="chat",
            idempotency_key="surface-signal",
            concurrency_key=compilation.package.definition.name,
            concurrency_policy="allow",
            operator_scope=operator_scope,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    claim = store.claim_node(admitted.run_id, "refine", "surface-worker")
    assert claim is not None
    store.mark_node_started(claim)
    result = _PRIVATE_PHASE4_PROVIDER_RESPONSE
    digest = hashlib.sha256(result).hexdigest()
    relative = (
        Path("nodes")
        / "refine"
        / claim.attempt_id
        / "iteration-0001"
        / "output.txt"
    )
    result_path = store.run_directory(admitted.run_id) / relative
    result_path.parent.mkdir(parents=True)
    result_path.write_bytes(result)
    pending = LoopSignalConfirmation.create(
        run_id=admitted.run_id,
        node_id="refine",
        message="Accept this result or provide feedback",
        iteration=1,
        max_iterations=3,
        result_artifact=relative.as_posix(),
        result_sha256=digest,
    ).to_dict()
    store.complete_node(
        claim,
        status="paused",
        artifacts=(ArtifactRef(relative.as_posix(), "text/plain", len(result), digest),),
        metadata={
            "pending_interaction": pending,
            "loop_state": {"iteration": 1},
        },
    )
    return store, admitted.run_id, pending


def _cli_status(home: Path, run_id: str, capsys) -> dict[str, object]:
    parser = argparse.ArgumentParser()
    register_cli(parser)
    args = parser.parse_args([
        "--hermes-home",
        str(home),
        "status",
        run_id,
        "--json",
    ])
    assert args.func(args) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is True
    return envelope["result"]


def _run_facts(run: Mapping[str, object]) -> tuple[object, object, object, object]:
    interaction = run["pending_interaction"]
    assert isinstance(interaction, Mapping)
    return (
        interaction["type"],
        run["state_version"],
        interaction["interaction_id"],
        run["next_actions"],
    )


def _dependency_compilation_v4(tmp_path: Path, workflow_writer):
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
    from plugins.workflow.schema import parse_workflow_source_bytes

    root_path = workflow_writer(
        tmp_path / "project/.hermes/workflows",
        name="surface-root",
        filename="surface-root.yaml",
        nodes=[{"id": "child", "include": "surface-child"}],
    )
    root_sidecar = (
        b"language_compatibility: archon-2026-07\n"
        b"delivery_defaults:\n"
        b"  inputs:\n"
        b"    private_context:\n"
        b"      type: text\n"
        b"      required: false\n"
        b"      default: PRIVATE_PHASE4_SECRET_VALUE\n"
    )
    root_path.with_name("surface-root.hermes.yaml").write_bytes(root_sidecar)
    child_path = workflow_writer(
        tmp_path / "home/workflows",
        name="surface-child",
        filename="surface-child.yaml",
        nodes=[
            {
                "id": "draft",
                "prompt": "PRIVATE_PHASE4_PROMPT_BODY",
            },
            {"id": "review", "command": "review", "depends_on": ["draft"]},
        ],
    )
    child_sidecar = (
        b"required_secrets: [SURFACE_API_KEY]\n"
        b"execution_environment: isolated_backend_required\n"
    )
    child_path.with_name("surface-child.hermes.yaml").write_bytes(child_sidecar)
    commands = tmp_path / "home/commands"
    commands.mkdir()
    (commands / "review.md").write_text(
        "PRIVATE_PHASE4_COMMAND_BODY\n",
        encoding="utf-8",
    )
    root = parse_workflow_source_bytes(
        root_path,
        workflow_bytes=root_path.read_bytes(),
        sidecar_bytes=root_sidecar,
        source="project",
        precedence=1,
    )
    child = parse_workflow_source_bytes(
        child_path,
        workflow_bytes=child_path.read_bytes(),
        sidecar_bytes=child_sidecar,
        source="profile",
        precedence=2,
    )
    return compile_workflow(
        root,
        WorkflowCatalogSnapshot.capture((root, child)),
        normalizer_version=4,
    )


def _assert_phase4_diagnostics(payload: Mapping[str, object], compilation) -> None:
    diagnostics = payload["compilation"]
    assert isinstance(diagnostics, Mapping)
    assert diagnostics["effective_profile"] == "archon-2026-07"
    assert diagnostics["normalizer_version"] == 4
    assert diagnostics["snapshot_format_version"] == 2
    assert diagnostics["dependency_manifest_schema_version"] == 1
    assert diagnostics["composite_digest"] == compilation.composite_digest
    assert diagnostics["counts"]["dependency_packages"] == 1
    assert diagnostics["counts"]["expanded_nodes"] == 2
    assert diagnostics["counts"]["expanded_edges"] == 1
    assert diagnostics["include_depth"] == 1
    assert diagnostics["dependencies"][0]["workflow_name"] == "surface-child"
    assert diagnostics["sources"][1]["catalog_source"] == "profile"
    assert diagnostics["sources"][1]["precedence"] == 2
    assert diagnostics["ignored_policies"][0]["fields"] == [
        "execution_environment",
        "required_secrets",
    ]
    assert diagnostics["origin_risks"]
    assert diagnostics["node_origins"]
    assert diagnostics["resource_origins"]


def test_phase4_compilation_diagnostics_reach_show_validate_doctor_and_detail(
    tmp_path,
    monkeypatch,
    workflow_writer,
    capsys,
) -> None:
    """Catch operator surfaces discarding the authenticated compilation closure."""
    compilation = _dependency_compilation_v4(tmp_path, workflow_writer)
    package = compilation.package
    compatibility = assess_compatibility(package)

    shown = show_package(
        package,
        compatibility_report=compatibility,
        compilation=compilation,
    )
    _assert_phase4_diagnostics(shown, compilation)

    monkeypatch.setattr(
        "plugins.workflow.cli._resolve_compilation",
        lambda *_args, **_kwargs: compilation,
    )
    monkeypatch.setattr(
        "plugins.workflow.cli._resolve",
        lambda *_args, **_kwargs: compilation.package,
    )
    parser = argparse.ArgumentParser()
    register_cli(parser)
    validate_args = parser.parse_args([
        "--hermes-home",
        str(tmp_path / "home"),
        "--workdir",
        str(tmp_path / "project"),
        "validate",
        "surface-root",
        "--json",
    ])
    assert validate_args.func(validate_args) == 0
    validated = json.loads(capsys.readouterr().out)["result"]
    _assert_phase4_diagnostics(validated, compilation)

    doctor = _doctor_payload(
        package,
        compilation=compilation,
        hermes_home=tmp_path / "home",
        compat_report=True,
    )
    _assert_phase4_diagnostics(doctor, compilation)
    assert {
        "phase4_sealed_closure",
        "phase4_future_admission_revalidation",
    } <= {finding["code"] for finding in doctor["findings"]}

    import plugins.workflow.catalog_api as catalog_api

    monkeypatch.setattr(
        catalog_api,
        "_discover_catalog_compilations",
        lambda *_args, **_kwargs: ((compilation,), False),
    )
    detail = catalog_api.build_workflow_detail(
        "surface-root",
        hermes_home=tmp_path / "home",
        workdir=tmp_path / "project",
    )
    _assert_phase4_diagnostics(detail, compilation)

    text_outputs = []
    for command in ("show", "validate", "doctor"):
        text_args = parser.parse_args([
            "--hermes-home",
            str(tmp_path / "home"),
            "--workdir",
            str(tmp_path / "project"),
            command,
            "surface-root",
        ])
        assert text_args.func(text_args) == 0
        text_outputs.append(capsys.readouterr().out)
    for output in (
        json.dumps(shown, sort_keys=True),
        json.dumps(validated, sort_keys=True),
        json.dumps(doctor, sort_keys=True),
        json.dumps(detail, sort_keys=True),
        *text_outputs,
    ):
        for private_value in (
            str(tmp_path),
            "PRIVATE_PHASE4_PROMPT_BODY",
            "PRIVATE_PHASE4_COMMAND_BODY",
            "PRIVATE_PHASE4_FEEDBACK",
            "PRIVATE_PHASE4_SECRET_VALUE",
            "PRIVATE_PHASE4_PROVIDER_RESPONSE",
        ):
            assert private_value not in output

    diagnostics = shown["compilation"]
    assert isinstance(diagnostics, Mapping)
    expected_text = (
        f"Dependencies ({len(diagnostics['dependencies'])}):",
        f"Sources and precedence ({len(diagnostics['sources'])}):",
        (
            "Expansion: "
            f"dependencies={diagnostics['counts']['dependency_packages']} "
            f"expanded_nodes={diagnostics['counts']['expanded_nodes']} "
            f"expanded_edges={diagnostics['counts']['expanded_edges']} "
            f"include_depth={diagnostics['include_depth']}"
        ),
        f"Ignored child policies ({len(diagnostics['ignored_policies'])}):",
        f"Logical node origins ({len(diagnostics['node_origins'])}):",
        f"Logical resource origins ({len(diagnostics['resource_origins'])}):",
        f"Per-origin risks ({len(diagnostics['origin_risks'])}):",
        "Diagnostics truncated: false",
        '"workflow_name":"surface-child"',
        '"catalog_source":"profile"',
    )
    for output in text_outputs:
        for fragment in expected_text:
            assert fragment in output


def test_paused_signal_has_one_backend_action_contract_across_every_surface(
    tmp_path,
    monkeypatch,
    workflow_writer,
    capsys,
) -> None:
    """Catch a surface omitting or independently inventing signal actions/state."""
    assert set(_EXPECTED_SIGNAL_ACTIONS) <= WIRE_ACTIONS
    operator_scope = "gateway:default:telegram:chat-1:user-1"
    store, run_id, pending = _paused_signal_run(
        tmp_path,
        workflow_writer,
        operator_scope=operator_scope,
    )
    home = store.hermes_home
    monkeypatch.setenv("HERMES_HOME", str(home))
    current = store.get_run_status(run_id, operator_scope=operator_scope)
    expected = (
        "loop_signal_confirmation",
        current["state_version"],
        pending["interaction_id"],
        _EXPECTED_SIGNAL_ACTIONS,
    )
    assert _run_facts(current) == expected

    cli_status = _cli_status(home, run_id, capsys)
    assert _run_facts(cli_status) == expected

    module = _api_module()
    with TestClient(_app(module.router)) as client:
        rest_status = client.get(f"/api/plugins/workflow/runs/{run_id}")
        attention = client.get("/api/plugins/workflow/attention")
        conflict = client.post(
            f"/api/plugins/workflow/runs/{run_id}/provide-input",
            json={
                "expected_version": int(current["state_version"]) - 1,
                "interaction_id": pending["interaction_id"],
                "value": "refresh me",
            },
        )
    assert rest_status.status_code == 200
    assert _run_facts(rest_status.json()) == expected
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "stale_state"
    assert _run_facts(conflict.json()["detail"]["current"]) == expected

    attention_item = next(
        item
        for item in attention.json()["items"]
        if item["run_id"] == run_id
        and item.get("kind") == "loop_signal_confirmation"
    )
    assert (
        attention_item["interaction"]["type"],
        attention_item["state_version"],
        attention_item["interaction"]["interaction_id"],
        attention_item["next_actions"],
    ) == expected

    notification = max(
        (
            item
            for item in NotificationOutbox(store).history(run_id=run_id)
            if isinstance(item.get("payload"), Mapping)
            and isinstance(item["payload"].get("interaction"), Mapping)
            and item["payload"]["interaction"].get("interaction_id")
            == pending["interaction_id"]
        ),
        key=lambda item: int(item["transition_version"]),
    )
    assert (
        notification["payload"]["interaction"]["type"],
        notification["payload"]["state_version"],
        notification["payload"]["interaction"]["interaction_id"],
        notification["payload"]["next_actions"],
    ) == expected

    evidence = EvidenceReader(store).query(
        run_id,
        kind="interactions",
        operator_scope=operator_scope,
    )
    evidence_item = next(
        item
        for item in evidence["items"]
        if item.get("type") == "loop_signal_confirmation"
    )
    assert (
        evidence_item["type"],
        evidence_item["state_version"],
        evidence_item["interaction_id"],
        evidence_item["next_actions"],
    ) == expected

    invocation = PluginInvocationContext(
        boundary="gateway",
        principal="gateway:telegram:user-1",
        operator_scope=operator_scope,
        assurance="verified_adapter",
        return_route_capability="opaque-capability",
    )
    captured: list[dict[str, object]] = []

    class RecordingStore:
        def provide_loop_input(self, selected_run_id, value, **kwargs):
            captured.append({
                "run_id": selected_run_id,
                "value": value,
                **kwargs,
            })
            return store.provide_loop_input(selected_run_id, value, **kwargs)

        def get_run_status(self, selected_run_id, **kwargs):
            return store.get_run_status(selected_run_id, **kwargs)

    monkeypatch.setattr(
        "plugins.workflow.cli._store",
        lambda *_args, **_kwargs: RecordingStore(),
    )
    gateway_conflict = json.loads(
        workflow_gateway_command(
            " ".join((
                "provide-input",
                run_id,
                "--interaction-id",
                str(pending["interaction_id"]),
                "--expected-version",
                str(int(current["state_version"]) - 1),
                "--value",
                "continue",
            )),
            invocation,
            hermes_home=home,
        )
    )
    assert gateway_conflict["ok"] is False
    assert gateway_conflict["error"] == "version_conflict"
    assert _run_facts(gateway_conflict["current"]) == expected

    gateway_wrong_interaction = json.loads(
        workflow_gateway_command(
            " ".join((
                "provide-input",
                run_id,
                "--interaction-id",
                f"{pending['interaction_id']}-wrong",
                "--expected-version",
                str(current["state_version"]),
                "--value",
                "wrong-interaction",
            )),
            invocation,
            hermes_home=home,
        )
    )
    assert gateway_wrong_interaction["ok"] is False
    assert gateway_wrong_interaction["error"] == "invalid_transition"
    assert gateway_wrong_interaction["message"] == (
        "workflow input transition is invalid"
    )
    assert _run_facts(gateway_wrong_interaction["current"]) == expected

    gateway_applied = json.loads(
        workflow_gateway_command(
            " ".join((
                "provide-input",
                run_id,
                "--interaction-id",
                str(pending["interaction_id"]),
                "--expected-version",
                str(current["state_version"]),
                "--value",
                _PRIVATE_PHASE4_FEEDBACK,
            )),
            invocation,
            hermes_home=home,
        )
    )
    assert gateway_applied["ok"] is True
    assert gateway_applied["result"]["action"] == "provide-input"
    assert captured[-1] == {
        "run_id": run_id,
        "value": _PRIVATE_PHASE4_FEEDBACK,
        "expected_state_version": current["state_version"],
        "interaction_id": pending["interaction_id"],
        "actor": invocation.principal,
        "channel": "gateway",
        "operator_scope": invocation.operator_scope,
    }

    updated = store.get_run_status(run_id, operator_scope=operator_scope)
    gateway_wrong_state = json.loads(
        workflow_gateway_command(
            " ".join((
                "provide-input",
                run_id,
                "--interaction-id",
                str(pending["interaction_id"]),
                "--expected-version",
                str(updated["state_version"]),
                "--value",
                "wrong-state",
            )),
            invocation,
            hermes_home=home,
        )
    )
    assert gateway_wrong_state["ok"] is False
    assert gateway_wrong_state["error"] == "invalid_transition"
    assert gateway_wrong_state["message"] == "workflow input transition is invalid"
    assert gateway_wrong_state["current"]["run_id"] == run_id
    assert gateway_wrong_state["current"]["state_version"] == updated["state_version"]
    assert gateway_wrong_state["current"]["next_actions"] == updated["next_actions"]

    updated_cli = _cli_status(home, run_id, capsys)
    with TestClient(_app(module.router)) as client:
        updated_rest = client.get(f"/api/plugins/workflow/runs/{run_id}")
        updated_attention = client.get("/api/plugins/workflow/attention")
    assert updated_rest.status_code == 200
    assert updated_attention.status_code == 200
    updated_notifications = NotificationOutbox(store).history(run_id=run_id)
    updated_evidence = EvidenceReader(store).query(
        run_id,
        kind="interactions",
        operator_scope=operator_scope,
    )

    public_surfaces = (
        current,
        cli_status,
        rest_status.json(),
        conflict.json()["detail"]["current"],
        attention.json(),
        notification,
        evidence,
        gateway_conflict,
        gateway_wrong_interaction,
        gateway_applied,
        updated,
        updated_cli,
        updated_rest.json(),
        updated_attention.json(),
        updated_notifications,
        updated_evidence,
        gateway_wrong_state,
    )
    for surface in public_surfaces:
        encoded = json.dumps(surface, sort_keys=True)
        assert _PRIVATE_PHASE4_FEEDBACK not in encoded
        assert _PRIVATE_PHASE4_PROVIDER_RESPONSE.decode().strip() not in encoded


def test_desktop_rest_applies_phase4_signal_actions(
    tmp_path,
    monkeypatch,
    workflow_writer,
) -> None:
    module = _api_module()
    for action in ("approve", "provide-input"):
        operator_scope = "local-admin"
        store, run_id, pending = _paused_signal_run(
            tmp_path / action,
            workflow_writer,
            operator_scope=operator_scope,
        )
        monkeypatch.setenv("HERMES_HOME", str(store.hermes_home))
        current = store.get_run_status(run_id, operator_scope=operator_scope)
        payload = {
            "expected_version": current["state_version"],
            "interaction_id": pending["interaction_id"],
        }
        if action == "provide-input":
            payload["value"] = "tighten evidence"
        with TestClient(_app(module.router)) as client:
            response = client.post(
                f"/api/plugins/workflow/runs/{run_id}/{action}",
                json=payload,
            )

        assert response.status_code == 200
        assert response.json()["run_id"] == run_id
        assert response.json()["state_version"] > current["state_version"]
