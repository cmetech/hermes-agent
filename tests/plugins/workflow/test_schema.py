from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from plugins.workflow import schema as schema_module
from plugins.workflow.models import WorkflowLanguageProfile, WorkflowValidationError
from plugins.workflow.schema import load_workflow, validate_package


def test_loads_all_seven_portable_node_types(workflow_writer, tmp_path):
    path = workflow_writer(
        tmp_path,
        persist_sessions=True,
        provider="claude",
        model="claude-sonnet",
        modelReasoningEffort="high",
        webSearchMode="auto",
        interactive=True,
        requires=["github"],
        worktree={"enabled": True},
        tags=["release"],
        effort="high",
        thinking={"type": "enabled", "budgetTokens": 2048},
        fallbackModel="fallback",
        betas=["feature"],
        sandbox={"enabled": True},
        nodes=[
            {
                "id": "command",
                "command": "review",
                "retry": {
                    "max_attempts": 3,
                    "delay_ms": 1000,
                    "on_error": "all",
                },
                "persist_session": True,
                "context": "fresh",
                "provider": "claude",
                "model": "sonnet",
                "output_type": "text",
                "allowed_tools": ["Read", "Grep"],
                "denied_tools": ["Bash"],
                "skills": ["review"],
                "mcp": "mcp/echo.yaml",
                "agents": {"reviewer": {"description": "Review", "prompt": "Check"}},
                "effort": "high",
                "thinking": {"type": "enabled", "budgetTokens": 1024},
                "maxBudgetUsd": 1.5,
                "systemPrompt": "You are careful.",
                "fallbackModel": "fallback",
                "betas": ["feature"],
                "sandbox": {"enabled": True},
                "output_format": {"type": "object"},
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "timeout": 10,
                            "response": {
                                "continue": True,
                                "decision": "approve",
                                "stopReason": "none",
                                "suppressOutput": False,
                                "systemMessage": "careful",
                                "hookSpecificOutput": {
                                    "hookEventName": "PreToolUse",
                                    "permissionDecision": "deny",
                                    "permissionDecisionReason": "read only",
                                    "updatedInput": {"path": "safe"},
                                    "additionalContext": "read only",
                                },
                            },
                        }
                    ]
                },
            },
            {
                "id": "prompt",
                "prompt": "Summarize",
                "depends_on": ["command"],
                "when": "$command.output != ''",
                "trigger_rule": "all_done",
                "idle_timeout": 1000,
                "always_run": True,
            },
            {"id": "bash", "bash": "true", "timeout": 1000, "depends_on": ["prompt"]},
            {
                "id": "script",
                "script": "print('ok')\n",
                "runtime": "uv",
                "deps": ["pydantic>=2"],
                "timeout": 1000,
                "depends_on": ["bash"],
            },
            {
                "id": "loop",
                "loop": {
                    "prompt": "Iterate",
                    "until": "DONE",
                    "max_iterations": 2,
                    "fresh_context": True,
                    "until_bash": "true",
                    "interactive": True,
                    "gate_message": "Continue?",
                },
                "depends_on": ["script"],
            },
            {
                "id": "approval",
                "approval": {
                    "message": "Proceed?",
                    "capture_response": True,
                    "on_reject": {
                        "prompt": "Revise: $REJECTION_REASON",
                        "max_attempts": 2,
                    },
                },
                "depends_on": ["loop"],
            },
            {"id": "cancel", "cancel": "Stop safely", "depends_on": ["approval"]},
        ],
    )

    package = load_workflow(path)

    assert [node.node_type for node in package.definition.nodes] == [
        "command",
        "prompt",
        "bash",
        "script",
        "loop",
        "approval",
        "cancel",
    ]
    assert validate_package(package) == ()
    with pytest.raises(FrozenInstanceError):
        package.definition.name = "changed"


def test_absent_companion_profile_defaults_to_legacy(workflow_writer, tmp_path):
    package = load_workflow(workflow_writer(tmp_path))

    assert package.language.declared_profile is None
    assert package.language.effective_profile.value == "hermes-legacy"


def test_companion_selects_archon_profile(workflow_writer, tmp_path):
    path = workflow_writer(tmp_path)
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )

    package = load_workflow(path)

    assert package.language.effective_profile.value == "archon-2026-07"
    assert package.source_definition == package.definition


@pytest.mark.parametrize("profile", tuple(WorkflowLanguageProfile))
def test_idle_timeout_loader_preserves_the_authored_runtime_value(
    workflow_writer, tmp_path, profile
):
    path = workflow_writer(
        tmp_path / profile.value,
        nodes=[{"id": "agent", "prompt": "x", "idle_timeout": 1_234.5}],
    )
    if profile is WorkflowLanguageProfile.ARCHON_2026_07:
        path.with_name(f"{path.stem}.hermes.yaml").write_text(
            f"language_compatibility: {profile.value}\n", encoding="utf-8"
        )

    package = load_workflow(path)

    assert package.definition.nodes[0].options["idle_timeout"] == 1_234.5


@pytest.mark.parametrize(
    "declaration",
    [
        "language_compatibility: archon-latest\n",
        "language_compatibility: null\n",
        "language_compatibility: true\n",
        "language_compatibility:\n  profile: archon-2026-07\n",
        "language_compatibility:\n  - archon-2026-07\n",
    ],
)
def test_invalid_companion_profile_is_rejected(
    workflow_writer, tmp_path, declaration
):
    path = workflow_writer(tmp_path)
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        declaration, encoding="utf-8"
    )

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(path)

    assert exc.value.issues[0].code == "workflow_language_profile_unsupported"


def test_unknown_top_level_field_remains_warning_for_legacy(
    workflow_writer, tmp_path
):
    package = load_workflow(workflow_writer(tmp_path, mystery=True))

    issue = next(
        item
        for item in package.validation_issues
        if item.code == "unknown_top_level_field"
    )
    assert issue.blocking is False


def test_unknown_top_level_field_blocks_archon_profile(workflow_writer, tmp_path):
    path = workflow_writer(tmp_path, mystery=True)
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(path)

    assert exc.value.issues[0].code == "archon_unknown_top_level_field"


def test_identical_bytes_loaded_from_installed_and_sealed_paths_have_same_digest(
    workflow_writer, tmp_path
):
    installed = workflow_writer(tmp_path / "installed")
    sealed = tmp_path / "run" / "definition.yaml"
    sealed.parent.mkdir()
    sealed.write_bytes(installed.read_bytes())

    assert (
        load_workflow(installed).language.normalized_definition_digest
        == load_workflow(sealed).language.normalized_definition_digest
    )


def test_loader_invokes_the_versioned_normalizer_exactly_once(
    workflow_writer, tmp_path, monkeypatch
):
    calls = 0
    normalize = schema_module.normalize_workflow

    def counting_normalize(*args, **kwargs):
        nonlocal calls
        calls += 1
        return normalize(*args, **kwargs)

    monkeypatch.setattr(schema_module, "normalize_workflow", counting_normalize)

    load_workflow(workflow_writer(tmp_path))

    assert calls == 1


@pytest.mark.parametrize(
    ("node", "message"),
    [
        ({"id": "bad", "prompt": "x", "bash": "echo x"}, "exactly one node type"),
        ({"id": "bad"}, "exactly one node type"),
        ({"id": "bad\nclick x", "bash": "true"}, "identifier"),
        ({"id": "bad\x1b[31m", "bash": "true"}, "identifier"),
        ({"id": "bad", "script": "x", "runtime": "node"}, "runtime"),
        ({"id": "bad", "script": "x"}, "runtime"),
        (
            {
                "id": "bad",
                "loop": {"prompt": "x", "until": "DONE", "max_iterations": 0},
            },
            "max_iterations",
        ),
        (
            {
                "id": "bad",
                "approval": {
                    "message": "x",
                    "on_reject": {"prompt": "x", "max_attempts": 11},
                },
            },
            "max_attempts",
        ),
    ],
)
def test_rejects_invalid_node_shapes(workflow_writer, tmp_path, node, message):
    path = workflow_writer(tmp_path, nodes=[node])
    with pytest.raises(WorkflowValidationError, match=message):
        load_workflow(path)


def test_rejects_removed_steps_key(workflow_writer, tmp_path):
    path = workflow_writer(tmp_path)
    text = path.read_text(encoding="utf-8").replace("nodes:", "steps:")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(WorkflowValidationError, match="steps.*removed"):
        load_workflow(path)


def test_rejects_legacy_kind_node_with_actionable_conversion(tmp_path):
    workflow = tmp_path / "legacy.yaml"
    workflow.write_text(
        "name: legacy\n"
        "description: old schema\n"
        "nodes:\n"
        "  - id: collect\n"
        "    kind: prompt\n"
        "    prompt: Collect evidence\n",
        encoding="utf-8",
    )

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(workflow)

    assert exc.value.issues[0].code == "legacy_kind_schema"
    assert "replace `kind: prompt`" in exc.value.issues[0].message


@pytest.mark.parametrize(
    ("nodes", "message"),
    [
        ([{"id": "same", "bash": "true"}, {"id": "same", "bash": "true"}], "duplicate"),
        (
            [{"id": "a", "bash": "true", "depends_on": ["missing"]}],
            "missing dependency",
        ),
        (
            [
                {"id": "a", "bash": "true", "depends_on": ["b"]},
                {"id": "b", "bash": "true", "depends_on": ["a"]},
            ],
            "cycle",
        ),
        ([{"id": "a", "bash": "true", "trigger_rule": "sometimes"}], "trigger_rule"),
        ([{"id": "a", "bash": "true", "retry": {"max_attempts": 0}}], "max_attempts"),
        ([{"id": "a", "bash": "true", "retry": {"max_attempts": 6}}], "max_attempts"),
        (
            [{"id": "a", "bash": "true", "retry": {"max_attempts": 2, "delay_ms": -1}}],
            "delay_ms",
        ),
        (
            [
                {
                    "id": "a",
                    "bash": "true",
                    "retry": {"max_attempts": 2, "delay_ms": 999},
                }
            ],
            "delay_ms",
        ),
        (
            [
                {
                    "id": "a",
                    "bash": "true",
                    "retry": {"max_attempts": 2, "on_error": "always"},
                }
            ],
            "on_error",
        ),
    ],
)
def test_rejects_invalid_dag_and_retry_contracts(
    workflow_writer, tmp_path, nodes, message
):
    path = workflow_writer(tmp_path, nodes=nodes)
    with pytest.raises(WorkflowValidationError, match=message):
        load_workflow(path)


@pytest.mark.parametrize("field", ["trust", "trusted", "package_trusted"])
def test_package_cannot_trust_itself(workflow_writer, tmp_path, field):
    path = workflow_writer(tmp_path, **{field: True})
    with pytest.raises(WorkflowValidationError, match="trust"):
        load_workflow(path)


@pytest.mark.parametrize(
    "value", ["../escape.yaml", "/tmp/escape.yaml", "mcp/../../escape.yaml"]
)
def test_rejects_resource_path_traversal(workflow_writer, tmp_path, value):
    path = workflow_writer(
        tmp_path,
        nodes=[{"id": "agent", "prompt": "x", "mcp": value}],
    )
    with pytest.raises(WorkflowValidationError, match="contained relative path"):
        load_workflow(path)


def test_unknown_execution_field_is_not_silently_ignored(workflow_writer, tmp_path):
    path = workflow_writer(
        tmp_path, nodes=[{"id": "a", "bash": "true", "shell_policy": "unsafe"}]
    )
    with pytest.raises(WorkflowValidationError, match="unknown execution field"):
        load_workflow(path)


def test_rejects_missing_and_malformed_condition_references(workflow_writer, tmp_path):
    missing = workflow_writer(
        tmp_path / "missing",
        nodes=[{"id": "a", "bash": "true", "when": "$missing.output == 'ok'"}],
    )
    with pytest.raises(WorkflowValidationError, match="missing condition reference"):
        load_workflow(missing)

    malformed = workflow_writer(
        tmp_path / "malformed",
        nodes=[{"id": "a", "bash": "true", "when": "run this maybe"}],
    )
    with pytest.raises(WorkflowValidationError, match="malformed condition"):
        load_workflow(malformed)


def test_sidecar_cannot_declare_trust_or_invalid_node_references(
    workflow_writer, tmp_path
):
    path = workflow_writer(tmp_path, filename="policy.yaml")
    path.with_name("policy.hermes.yaml").write_text("trusted: true\n", encoding="utf-8")
    with pytest.raises(WorkflowValidationError, match="cannot set trust"):
        load_workflow(path)

    path.with_name("policy.hermes.yaml").write_text(
        "outward_action_nodes: [missing]\n", encoding="utf-8"
    )
    with pytest.raises(WorkflowValidationError, match="unknown node"):
        load_workflow(path)


def test_sidecar_rejects_run_level_max_iterations(workflow_writer, tmp_path):
    path = workflow_writer(tmp_path, filename="bounded.yaml")
    path.with_name("bounded.hermes.yaml").write_text(
        "limits:\n  max_iterations: 12\n", encoding="utf-8"
    )

    with pytest.raises(WorkflowValidationError, match="max_iterations"):
        load_workflow(path)


@pytest.mark.parametrize(
    "nodes",
    [
        [{"id": "a", "bash": "true", "always_run": "yes"}],
        [{"id": "a", "prompt": "x", "allowed_tools": "Read"}],
        [{"id": "a", "prompt": "x", "effort": "extreme"}],
        [{"id": "a", "prompt": "x", "output_format": "json"}],
        [{"id": "a", "prompt": "x", "maxBudgetUsd": -1}],
        [{"id": "a", "bash": "true", "timeout": False}],
    ],
)
def test_published_field_types_are_validated(workflow_writer, tmp_path, nodes):
    with pytest.raises(WorkflowValidationError):
        load_workflow(workflow_writer(tmp_path, nodes=nodes))


def test_condition_must_reference_an_upstream_node(workflow_writer, tmp_path):
    path = workflow_writer(
        tmp_path,
        nodes=[
            {"id": "source", "bash": "true"},
            {"id": "sibling", "bash": "true"},
            {
                "id": "consumer",
                "bash": "true",
                "depends_on": ["source"],
                "when": "$sibling.output == 'ok'",
            },
        ],
    )
    with pytest.raises(WorkflowValidationError, match="not upstream"):
        load_workflow(path)


@pytest.mark.parametrize("field", ["timeout", "idle_timeout"])
def test_rejects_non_finite_execution_deadlines(workflow_writer, tmp_path, field):
    node = {"id": "a", "bash": "true", field: float("inf")}
    path = workflow_writer(tmp_path / field, nodes=[node])
    with pytest.raises(WorkflowValidationError, match="finite"):
        load_workflow(path)
