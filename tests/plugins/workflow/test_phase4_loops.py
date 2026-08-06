from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.language import (
    WorkflowLanguageCompatibilityError,
    make_language_snapshot,
    read_language_snapshot,
    verify_language_snapshot,
)
from plugins.workflow.models import WorkflowValidationError
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import parse_workflow_source_bytes
from plugins.workflow.store import RunStore
from plugins.workflow.trust import (
    WorkflowPackageDigest,
    build_risk_summary,
)


def _compile_version(path: Path, version: int):
    sidecar = b"language_compatibility: archon-2026-07\n"
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
        normalizer_version=version,
    )


def _compile_v4(path: Path):
    return _compile_version(path, 4)


def _write_loop(
    tmp_path: Path,
    loop: dict[str, object],
    *,
    workflow_interactive: bool = False,
) -> Path:
    path = tmp_path / "workflows" / "loop.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, object] = {
        "name": "loop",
        "description": "Loop contract fixture",
        "nodes": [{"id": "refine", "loop": loop}],
    }
    if workflow_interactive:
        document["interactive"] = True
    path.write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )
    return path


def test_v4_inline_loop_projects_noninteractive_default_semantics(
    tmp_path: Path,
) -> None:
    path = tmp_path / "workflows" / "inline-loop.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump(
            {
                "name": "inline-loop",
                "description": "Inline loop semantics",
                "nodes": [
                    {
                        "id": "refine",
                        "loop": {
                            "prompt": "Refine the result",
                            "until": "DONE",
                            "max_iterations": 2,
                        },
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    compilation = _compile_v4(path)

    assert compilation.package.language.node_semantics["refine"]["loop"] == {
        "prompt_source": "inline",
        "command_binding": None,
        "effective_interactive": False,
        "signal_completes": True,
    }


def test_v4_command_loop_projects_only_its_collision_proof_sealed_binding(
    tmp_path: Path,
) -> None:
    path = tmp_path / "package" / "workflows" / "command-loop.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump(
            {
                "name": "command-loop",
                "description": "Command loop semantics",
                "nodes": [
                    {
                        "id": "refine",
                        "loop": {
                            "command": "refine",
                            "until": "DONE",
                            "max_iterations": 2,
                        },
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    commands = path.parent.parent / "commands"
    commands.mkdir()
    commands.joinpath("refine.md").write_text(
        "---\ndescription: Refine a result\n---\nRefine the result\n",
        encoding="utf-8",
    )

    compilation = _compile_v4(path)

    binding = next(
        item
        for item in compilation.dependency_manifest.resources
        if item.resource_kind == "loop_command"
    )
    assert compilation.package.language.node_semantics["refine"]["loop"] == {
        "prompt_source": "command",
        "command_binding": binding.snapshot_path,
        "effective_interactive": False,
        "signal_completes": True,
    }
    assert "Refine the result" not in str(
        compilation.package.language.node_semantics
    )
    risk = build_risk_summary(
        compilation.package,
        assess_compatibility(compilation.package),
        compilation=compilation,
    )
    assert "Refine the result" not in str(risk.to_dict())


@pytest.mark.parametrize(
    ("workflow_interactive", "loop_interactive", "authored_signal", "expected"),
    [
        (False, False, None, True),
        (True, True, None, False),
        (True, True, True, True),
    ],
)
def test_v4_loop_signal_defaults_follow_effective_interactivity(
    tmp_path: Path,
    workflow_interactive: bool,
    loop_interactive: bool,
    authored_signal: bool | None,
    expected: bool,
) -> None:
    loop: dict[str, object] = {
        "prompt": "Refine",
        "until": "DONE",
        "max_iterations": 2,
        "interactive": loop_interactive,
    }
    if loop_interactive:
        loop["gate_message"] = "Accept or refine"
    if authored_signal is not None:
        loop["signal_completes"] = authored_signal

    compilation = _compile_v4(
        _write_loop(
            tmp_path,
            loop,
            workflow_interactive=workflow_interactive,
        )
    )

    semantics = compilation.package.language.node_semantics["refine"]["loop"]
    assert semantics["effective_interactive"] is (
        workflow_interactive and loop_interactive
    )
    assert semantics["signal_completes"] is expected


def test_v4_rejects_signal_confirmation_without_an_effective_operator_path(
    tmp_path: Path,
) -> None:
    path = _write_loop(
        tmp_path,
        {
            "prompt": "Refine",
            "until": "DONE",
            "max_iterations": 2,
            "signal_completes": False,
        },
    )

    with pytest.raises(WorkflowValidationError) as raised:
        _compile_v4(path)

    assert raised.value.issues[0].code == (
        "archon_loop_signal_confirmation_unavailable"
    )


def test_v4_command_loop_snapshot_reloads_only_the_authenticated_binding(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "source"
    path = _write_loop(
        package_root,
        {
            "command": "refine",
            "until": "DONE",
            "max_iterations": 2,
        },
    )
    commands = package_root / "commands"
    commands.mkdir()
    commands.joinpath("refine.md").write_text(
        "---\ndescription: Refine\n---\nUse only sealed bytes.\n",
        encoding="utf-8",
    )
    compilation = _compile_v4(path)
    binding = next(
        item
        for item in compilation.dependency_manifest.resources
        if item.resource_kind == "loop_command"
    )
    store = RunStore(tmp_path / "home")
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
            workflow_name="loop",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="sealed-loop-command",
            concurrency_key="loop",
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    commands.joinpath("refine.md").write_text(
        "MUTATED SOURCE MUST NOT WIN\n",
        encoding="utf-8",
    )

    reloaded = RunScheduler(RunStore(tmp_path / "home"))._load_run_package(
        admitted.run_id
    )

    assert reloaded.language.node_semantics["refine"]["loop"] == {
        "prompt_source": "command",
        "command_binding": binding.snapshot_path,
        "effective_interactive": False,
        "signal_completes": True,
    }
    assert reloaded.definition.nodes[0].value["command"] == binding.snapshot_path
    assert (
        reloaded.root.joinpath(binding.snapshot_path).read_text(encoding="utf-8")
        == "---\ndescription: Refine\n---\nUse only sealed bytes.\n"
    )

    sealed_command = store.run_directory(admitted.run_id) / binding.snapshot_path
    sealed_command.write_text("TAMPERED SEALED COMMAND\n", encoding="utf-8")

    with pytest.raises(WorkflowLanguageCompatibilityError) as raised:
        RunScheduler(store)._load_run_package(admitted.run_id)

    assert raised.value.code == "workflow_snapshot_integrity_mismatch"


def test_v4_rejects_an_authenticated_loop_command_with_an_empty_body(
    tmp_path: Path,
) -> None:
    path = _write_loop(
        tmp_path,
        {
            "command": "refine",
            "until": "DONE",
            "max_iterations": 2,
        },
    )
    commands = tmp_path / "commands"
    commands.mkdir()
    commands.joinpath("refine.md").write_text(
        "---\ndescription: Empty command\n---\n   \n",
        encoding="utf-8",
    )

    with pytest.raises(WorkflowValidationError) as raised:
        _compile_v4(path)

    assert raised.value.issues[0].code == "invalid_command_resource"


def test_v4_rejects_missing_and_invalid_loop_command_resources(
    tmp_path: Path,
) -> None:
    missing_path = _write_loop(
        tmp_path / "missing",
        {"command": "refine", "until": "DONE", "max_iterations": 2},
    )
    with pytest.raises(WorkflowValidationError) as missing:
        _compile_v4(missing_path)
    assert missing.value.issues[0].code == "missing_command"

    invalid_path = _write_loop(
        tmp_path / "invalid",
        {"command": "refine", "until": "DONE", "max_iterations": 2},
    )
    commands = tmp_path / "invalid" / "commands"
    commands.mkdir()
    commands.joinpath("refine.md").write_text(
        "---\ndescription: unterminated\n",
        encoding="utf-8",
    )
    with pytest.raises(WorkflowValidationError) as invalid:
        _compile_v4(invalid_path)
    assert invalid.value.issues[0].code == "invalid_command_resource"


@pytest.mark.parametrize(
    "loop",
    [
        {
            "prompt": "Refine",
            "command": "refine",
            "until": "DONE",
            "max_iterations": 2,
        },
        {"until": "DONE", "max_iterations": 2},
        {"prompt": " ", "until": "DONE", "max_iterations": 2},
        {"command": " ", "until": "DONE", "max_iterations": 2},
        {"prompt": "Refine", "until": " ", "max_iterations": 2},
        {"prompt": "Refine", "until": "DONE", "max_iterations": 0},
        {"prompt": "Refine", "until": "DONE", "max_iterations": 101},
        {"prompt": "Refine", "until": "DONE", "max_iterations": True},
        {
            "prompt": "Refine",
            "until": "DONE",
            "max_iterations": 2,
            "interactive": "yes",
        },
        {
            "prompt": "Refine",
            "until": "DONE",
            "max_iterations": 2,
            "signal_completes": 1,
        },
        {
            "prompt": "Refine",
            "until": "DONE",
            "max_iterations": 2,
            "unknown": True,
        },
        {
            "prompt": "Refine",
            "until": "DONE",
            "max_iterations": 2,
            "interactive": True,
        },
        {
            "prompt": "Refine",
            "until": "DONE",
            "max_iterations": 2,
            "interactive": True,
            "gate_message": " ",
        },
    ],
)
def test_v4_loop_schema_rejects_every_invalid_exact_shape(
    tmp_path: Path,
    loop: dict[str, object],
) -> None:
    with pytest.raises(WorkflowValidationError):
        _compile_v4(_write_loop(tmp_path, loop, workflow_interactive=True))


@pytest.mark.parametrize("maximum", [1, 100])
def test_v4_accepts_exact_loop_iteration_bounds_and_inherited_fields(
    tmp_path: Path,
    maximum: int,
) -> None:
    until_bash = "test -s $LOOP_OUTPUT_FILE"
    compilation = _compile_v4(
        _write_loop(
            tmp_path,
            {
                "prompt": "Refine",
                "until": "DONE",
                "max_iterations": maximum,
                "fresh_context": True,
                "until_bash": until_bash,
            },
        )
    )

    loop = compilation.package.definition.nodes[0].value
    assert loop["max_iterations"] == maximum
    assert loop["fresh_context"] is True
    assert loop["until_bash"] == until_bash


@pytest.mark.parametrize("version", [1, 2, 3])
@pytest.mark.parametrize(
    "phase4_field",
    [
        {"command": "refine"},
        {"prompt": "Refine", "signal_completes": True},
    ],
)
def test_v1_through_v3_reject_phase4_loop_source_fields(
    tmp_path: Path,
    version: int,
    phase4_field: dict[str, object],
) -> None:
    loop = {"until": "DONE", "max_iterations": 2, **phase4_field}

    with pytest.raises(WorkflowValidationError) as raised:
        _compile_version(_write_loop(tmp_path, loop), version)

    assert raised.value.issues[0].code == "unknown_loop_field"


def test_v3_inline_loop_source_and_snapshot_shape_remain_unchanged(
    tmp_path: Path,
) -> None:
    authored_loop = {
        "prompt": "Refine",
        "until": "DONE",
        "max_iterations": 2,
        "fresh_context": True,
    }
    package = _compile_version(_write_loop(tmp_path, authored_loop), 3).package

    assert dict(package.definition.nodes[0].value) == authored_loop
    assert "refine" not in package.language.node_semantics
    snapshot = make_language_snapshot(package, "a" * 64).to_dict()
    assert read_language_snapshot(snapshot).to_dict() == snapshot


@pytest.mark.parametrize(
    "mutation",
    [
        lambda loop: loop.update({"extra": True}),
        lambda loop: loop.pop("signal_completes"),
        lambda loop: loop.update({"command_binding": "commands/refine.md"}),
        lambda loop: loop.update({"prompt_source": "inline"}),
        lambda loop: loop.update(
            {"effective_interactive": False, "signal_completes": False}
        ),
    ],
)
def test_v4_language_snapshot_rejects_malformed_loop_semantics(
    tmp_path: Path,
    mutation,
) -> None:
    path = _write_loop(
        tmp_path,
        {"command": "refine", "until": "DONE", "max_iterations": 2},
    )
    commands = tmp_path / "commands"
    commands.mkdir()
    commands.joinpath("refine.md").write_text("Refine safely.\n", encoding="utf-8")
    compilation = _compile_v4(path)
    snapshot = make_language_snapshot(
        compilation.package, compilation.composite_digest
    ).to_dict()
    corrupted = deepcopy(snapshot)
    mutation(corrupted["node_semantics"]["refine"]["loop"])

    with pytest.raises(WorkflowLanguageCompatibilityError) as raised:
        read_language_snapshot(corrupted)

    assert raised.value.code == "workflow_language_snapshot_invalid"


def test_v4_language_snapshot_rejects_a_different_well_formed_binding(
    tmp_path: Path,
) -> None:
    path = _write_loop(
        tmp_path,
        {"command": "refine", "until": "DONE", "max_iterations": 2},
    )
    commands = tmp_path / "commands"
    commands.mkdir()
    commands.joinpath("refine.md").write_text("Refine safely.\n", encoding="utf-8")
    compilation = _compile_v4(path)
    snapshot = make_language_snapshot(
        compilation.package, compilation.composite_digest
    ).to_dict()
    snapshot["node_semantics"]["refine"]["loop"]["command_binding"] = (
        "packages/" + "a" * 64 + "/" + "b" * 64 + "/commands/refine.md"
    )
    parsed = read_language_snapshot(snapshot)
    assert parsed is not None

    with pytest.raises(WorkflowLanguageCompatibilityError) as raised:
        verify_language_snapshot(
            compilation.package,
            compilation.composite_digest,
            parsed,
        )

    assert raised.value.code == "workflow_language_snapshot_mismatch"


def test_v4_loop_semantics_cannot_attach_to_the_legacy_profile(
    tmp_path: Path,
) -> None:
    package = _compile_version(
        _write_loop(
            tmp_path,
            {"prompt": "Refine", "until": "DONE", "max_iterations": 2},
        ),
        3,
    ).package
    snapshot = make_language_snapshot(package, "a" * 64).to_dict()
    snapshot["effective_profile"] = "hermes-legacy"
    snapshot["normalizer_version"] = 4
    snapshot["node_semantics"] = {
        "refine": {
            "loop": {
                "prompt_source": "inline",
                "command_binding": None,
                "effective_interactive": False,
                "signal_completes": True,
            }
        }
    }

    with pytest.raises(WorkflowLanguageCompatibilityError) as raised:
        read_language_snapshot(snapshot)

    assert raised.value.code == "workflow_normalizer_version_unsupported"


def test_included_loop_command_uses_child_origin_and_rewritten_sealed_body(
    tmp_path: Path,
) -> None:
    root_path = tmp_path / "root" / "workflows" / "root.yaml"
    root_path.parent.mkdir(parents=True)
    root_path.write_text(
        yaml.safe_dump(
            {
                "name": "root",
                "description": "Root without interactive authority",
                "nodes": [{"id": "child", "include": "child"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    child_path = tmp_path / "child" / "workflows" / "child.yaml"
    child_path.parent.mkdir(parents=True)
    child_path.write_text(
        yaml.safe_dump(
            {
                "name": "child",
                "description": "Child command loop",
                "interactive": True,
                "nodes": [
                    {"id": "prepare", "prompt": "Prepare"},
                    {
                        "id": "refine",
                        "loop": {
                            "command": "refine",
                            "until": "DONE",
                            "max_iterations": 2,
                            "interactive": True,
                            "gate_message": "Accept",
                        },
                        "depends_on": ["prepare"],
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    child_commands = child_path.parent.parent / "commands"
    child_commands.mkdir()
    child_commands.joinpath("refine.md").write_text(
        "Refine $prepare.output without exposing this body.\n",
        encoding="utf-8",
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    root = parse_workflow_source_bytes(
        root_path,
        workflow_bytes=root_path.read_bytes(),
        sidecar_bytes=sidecar,
        source="project",
        precedence=1,
    )
    child = parse_workflow_source_bytes(
        child_path,
        workflow_bytes=child_path.read_bytes(),
        sidecar_bytes=None,
        source="profile",
        precedence=2,
    )

    compilation = compile_workflow(
        root,
        WorkflowCatalogSnapshot.capture((root, child)),
        normalizer_version=4,
    )

    semantics = compilation.package.language.node_semantics["child__refine"]["loop"]
    binding = next(
        item
        for item in compilation.dependency_manifest.resources
        if item.resource_kind == "loop_command"
    )
    assert binding.package_key == "profile:child"
    assert semantics == {
        "prompt_source": "command",
        "command_binding": binding.snapshot_path,
        "effective_interactive": False,
        "signal_completes": True,
    }
    assert compilation.sealed_files[binding.snapshot_path] == (
        b"Refine $child__prepare.output without exposing this body.\n"
    )
    assert "without exposing this body" not in str(
        compilation.package.language.node_semantics
    )
