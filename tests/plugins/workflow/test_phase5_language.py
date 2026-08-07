from __future__ import annotations

from copy import deepcopy

import pytest

import plugins.workflow.language as workflow_language
from plugins.workflow.language import (
    WorkflowLanguageCompatibilityError,
    make_language_snapshot,
    read_language_snapshot,
    supports_phase3_semantics,
    supports_phase4_semantics,
    supports_phase5_semantics,
    supports_structured_outputs,
)
from plugins.workflow.language_schema import definition_json_schema
from plugins.workflow.models import WorkflowLanguageProfile, WorkflowValidationError
from plugins.workflow.resources import normalize_mcp_server_document
from plugins.workflow.schema import load_workflow, load_workflow_snapshot


def _sidecar(path) -> bytes:
    sidecar = path.with_name(f"{path.stem}.hermes.yaml")
    sidecar.write_text("language_compatibility: archon-2026-07\n", encoding="utf-8")
    return sidecar.read_bytes()


def _load_v5(path):
    return load_workflow_snapshot(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=_sidecar(path),
        normalizer_version=5,
    )


@pytest.mark.parametrize(
    ("version", "structured", "phase3", "phase4", "phase5"),
    [
        (1, False, False, False, False),
        (2, True, False, False, False),
        (3, True, True, False, False),
        (4, True, True, True, False),
        (5, True, True, True, True),
    ],
)
def test_archon_v5_capabilities_are_cumulative(
    version, structured, phase3, phase4, phase5
):
    profile = WorkflowLanguageProfile.ARCHON_2026_07

    assert supports_structured_outputs(profile, version) is structured
    assert supports_phase3_semantics(profile, version) is phase3
    assert supports_phase4_semantics(profile, version) is phase4
    assert supports_phase5_semantics(profile, version) is phase5


def test_v5_is_readable_but_current_archon_admission_remains_v4(
    tmp_path, workflow_writer
):
    path = workflow_writer(tmp_path, nodes=[{"id": "ask", "prompt": "hello"}])
    _sidecar(path)

    current = load_workflow(path)
    explicit = load_workflow_snapshot(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=path.with_name(f"{path.stem}.hermes.yaml").read_bytes(),
        normalizer_version=5,
    )

    assert workflow_language.LATEST_NORMALIZER_VERSION == 4
    assert (
        workflow_language.CURRENT_NORMALIZER_BY_PROFILE[
            WorkflowLanguageProfile.ARCHON_2026_07
        ]
        == 4
    )
    assert workflow_language.SUPPORTED_NORMALIZER_VERSIONS == {1, 2, 3, 4, 5}
    assert current.language.normalizer_version == 4
    assert explicit.language.normalizer_version == 5
    assert "provider_portability" not in current.language.node_semantics["ask"]


def test_v5_tags_model_references_and_canonicalizes_hook_obligations(
    tmp_path, workflow_writer
):
    path = workflow_writer(
        tmp_path,
        model="small",
        fallbackModel="@recovery",
        nodes=[
            {
                "id": "ask",
                "prompt": "hello",
                "mcp": "mcp/local.yaml",
                "agents": {
                    "reviewer": {
                        "description": "Review",
                        "prompt": "Review the result",
                        "model": "vendor/literal-model",
                    }
                },
                "hooks": {
                    "Notification": [
                        {
                            "matcher": None,
                            "timeout": 12,
                            "response": {
                                "continue": True,
                                "suppressOutput": True,
                            },
                        }
                    ],
                    "PreToolUse": [
                        {
                            "matcher": "^read_file$",
                            "response": {
                                "hookSpecificOutput": {
                                    "hookEventName": "PreToolUse",
                                    "permissionDecision": "deny",
                                    "permissionDecisionReason": "policy",
                                    "updatedInput": {"path": "safe.txt"},
                                }
                            },
                        }
                    ],
                },
            },
            {
                "id": "loop",
                "loop": {
                    "prompt": "again",
                    "until": "done",
                    "max_iterations": 2,
                },
            },
        ],
    )
    (tmp_path / "mcp").mkdir()
    (tmp_path / "mcp" / "local.yaml").write_text(
        "echo:\n  command: python\n", encoding="utf-8"
    )

    package = _load_v5(path)
    portability = package.language.node_semantics["ask"]["provider_portability"]

    assert portability["model_references"] == {
        "fallback": {"kind": "configured_alias", "reference": "@recovery"},
        "inline_agent:reviewer": {
            "kind": "literal",
            "reference": "vendor/literal-model",
        },
        "primary": {"kind": "tier", "reference": "small"},
    }
    assert portability["mcp_reference"] == "mcp/local.yaml"
    assert portability["hooks"] == (
        {
            "event": "Notification",
            "hermes_event": None,
            "matcher": None,
            "operations": (
                {"name": "continue", "value": True},
                {"name": "suppress_output", "value": True},
            ),
            "timeout_seconds": 12.0,
        },
        {
            "event": "PreToolUse",
            "hermes_event": "pre_tool_call",
            "matcher": "^read_file$",
            "operations": (
                {"name": "permission_decision", "value": "deny"},
                {"name": "permission_decision_reason", "value": "policy"},
                {"name": "update_input", "value": {"path": "safe.txt"}},
            ),
            "timeout_seconds": 30.0,
        },
    )
    assert package.language.node_semantics["loop"]["loop"]["prompt_source"] == "inline"

    snapshot = make_language_snapshot(package, "a" * 64).to_dict()
    assert read_language_snapshot(snapshot).to_dict() == snapshot


def test_v5_model_reference_tags_follow_resolver_whitespace_semantics(
    tmp_path, workflow_writer
):
    path = workflow_writer(
        tmp_path,
        nodes=[{"id": "ask", "prompt": "hello", "model": "  medium  "}],
    )

    package = _load_v5(path)

    assert package.language.node_semantics["ask"]["provider_portability"][
        "model_references"
    ]["primary"] == {"kind": "tier", "reference": "medium"}


@pytest.mark.parametrize("matcher", [None, "^tool_[a-z]+$"])
def test_v5_hook_matcher_accepts_only_bounded_string_or_null(
    tmp_path, workflow_writer, matcher
):
    path = workflow_writer(
        tmp_path,
        nodes=[
            {
                "id": "ask",
                "prompt": "hello",
                "hooks": {
                    "PreToolUse": [{"matcher": matcher, "response": {"continue": True}}]
                },
            }
        ],
    )

    package = _load_v5(path)

    hook = package.language.node_semantics["ask"]["provider_portability"]["hooks"][0]
    assert hook["matcher"] == matcher


@pytest.mark.parametrize("matcher", [7, "[", "x" * 513])
def test_v5_hook_matcher_rejects_invalid_or_unbounded_values(
    tmp_path, workflow_writer, matcher
):
    path = workflow_writer(
        tmp_path,
        nodes=[
            {
                "id": "ask",
                "prompt": "hello",
                "hooks": {
                    "PreToolUse": [{"matcher": matcher, "response": {"continue": True}}]
                },
            }
        ],
    )

    with pytest.raises((WorkflowValidationError, ValueError)):
        _load_v5(path)


def test_v5_hook_count_is_bounded(tmp_path, workflow_writer):
    path = workflow_writer(
        tmp_path,
        nodes=[
            {
                "id": "ask",
                "prompt": "hello",
                "hooks": {
                    "PreToolUse": [{"response": {"continue": True}} for _ in range(65)]
                },
            }
        ],
    )

    with pytest.raises(ValueError, match="hook obligations"):
        _load_v5(path)


def test_v5_mcp_wrapper_spellings_normalize_identically():
    servers = {
        "echo": {
            "command": "python",
            "args": ["server.py"],
            "connect_timeout": 10,
        }
    }

    expected = normalize_mcp_server_document(servers, default_name="local")

    assert (
        normalize_mcp_server_document({"mcp_servers": servers}, default_name="local")
        == expected
    )
    assert (
        normalize_mcp_server_document({"mcpServers": servers}, default_name="local")
        == expected
    )
    assert (
        normalize_mcp_server_document(servers["echo"], default_name="echo") == expected
    )
    assert expected["echo"]["transport"] == "stdio"


def test_v5_mcp_conflicting_wrappers_fail_closed():
    with pytest.raises(ValueError, match="conflicting MCP wrappers"):
        normalize_mcp_server_document(
            {
                "mcp_servers": {"one": {"command": "python"}},
                "mcpServers": {"two": {"command": "python"}},
            },
            default_name="local",
        )


@pytest.mark.parametrize(
    "server",
    [
        {"command": "python", "url": "https://mcp.example.test"},
        {"command": "python", "transport": []},
        {"url": "https://mcp.example.test", "transport": {}},
        {"command": "python", "connect_timeout": float("nan")},
    ],
)
def test_v5_mcp_canonicalizer_rejects_ambiguous_or_unbounded_servers(server):
    with pytest.raises(ValueError):
        normalize_mcp_server_document({"server": server}, default_name="local")


def test_v5_semantic_mutation_changes_normalized_digest(tmp_path, workflow_writer):
    base = {
        "id": "ask",
        "prompt": "hello",
        "model": "medium",
        "hooks": {
            "PreToolUse": [{"matcher": "^read_file$", "response": {"continue": True}}]
        },
    }
    first = workflow_writer(tmp_path / "first", nodes=[base])
    changed = deepcopy(base)
    changed["hooks"]["PreToolUse"][0]["matcher"] = "^write_file$"
    second = workflow_writer(tmp_path / "second", nodes=[changed])

    assert (
        _load_v5(first).language.normalized_definition_digest
        != _load_v5(second).language.normalized_definition_digest
    )


def test_explicit_v5_schema_is_bounded_without_changing_current_v4_schema():
    profile = WorkflowLanguageProfile.ARCHON_2026_07
    current = definition_json_schema(profile)
    v4 = definition_json_schema(profile, normalizer_version=4)
    v5 = definition_json_schema(profile, normalizer_version=5)
    matcher = v5["properties"]["nodes"]["items"]["properties"]["hooks"]["properties"][
        "PreToolUse"
    ]["items"]["properties"]["matcher"]

    assert current == v4
    assert matcher["type"] == ["string", "null"]
    assert matcher["maxLength"] == 512


def test_v5_snapshot_reader_rejects_unknown_portability_material(
    tmp_path, workflow_writer
):
    path = workflow_writer(
        tmp_path,
        nodes=[{"id": "ask", "prompt": "hello", "model": "large"}],
    )
    snapshot = make_language_snapshot(_load_v5(path), "b" * 64).to_dict()
    snapshot["node_semantics"]["ask"]["provider_portability"]["unknown"] = True

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        read_language_snapshot(snapshot)

    assert exc.value.code == "workflow_language_snapshot_invalid"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda portability: portability["model_references"]["primary"].update({
            "kind": []
        }),
        lambda portability: portability["hooks"][0].update({"event": []}),
        lambda portability: portability["hooks"][0]["operations"][0].update({
            "name": []
        }),
    ],
)
def test_v5_snapshot_reader_wraps_malformed_nested_values(
    tmp_path, workflow_writer, mutation
):
    path = workflow_writer(
        tmp_path,
        nodes=[
            {
                "id": "ask",
                "prompt": "hello",
                "model": "large",
                "hooks": {"PreToolUse": [{"response": {"continue": True}}]},
            }
        ],
    )
    snapshot = make_language_snapshot(_load_v5(path), "c" * 64).to_dict()
    mutation(snapshot["node_semantics"]["ask"]["provider_portability"])

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        read_language_snapshot(snapshot)

    assert exc.value.code == "workflow_language_snapshot_invalid"
