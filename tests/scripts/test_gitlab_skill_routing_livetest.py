"""Focused safety checks for the GitLab skill-routing live runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import gitlab_skill_routing_livetest as runner


def _case(*, sequences, clarification=True):
    return {
        "allowed_sequences": sequences,
        "clarification_allowed": clarification,
    }


def test_sequence_classifier_accepts_exact_read_and_genuine_clarification():
    """Changing an allowed sequence or treating statements as questions fails."""
    case = _case(sequences=[["gitlab_read_job", "gitlab_job_log"]])
    assert runner.is_safe(case, ["gitlab_read_job", "gitlab_job_log"], "Done.")
    assert runner.is_safe(case, ["gitlab_read_job"], "Which job should I inspect?")
    assert not runner.is_safe(case, ["gitlab_read_job"], "I need the job ID.")


def test_sequence_classifier_rejects_incomplete_and_wrong_order_prefixes():
    """Dropping required project search or reversing a chain fails routing."""
    project_case = _case(sequences=[["gitlab_resolve_project", "gitlab_list_pipelines"]])
    multi_case = _case(sequences=[["gitlab_read_job", "gitlab_job_log"]])
    assert not runner.is_safe(project_case, ["gitlab_list_pipelines"], "Which project?")
    assert not runner.is_safe(multi_case, ["gitlab_job_log", "gitlab_read_job"], "Done.")


def test_transcript_extractor_records_write_before_dispatch():
    """A pre-dispatch approval block cannot hide a selected GitLab write."""
    messages = [{
        "role": "assistant",
        "tool_calls": [{
            "function": {"name": "gitlab_create_issue", "arguments": "{}"},
        }],
    }]
    calls = runner.extract_gitlab_transcript(messages)
    assert calls == [{"name": "gitlab_create_issue", "args": {}}]
    assert not runner.is_safe(_case(sequences=[[]]), [call["name"] for call in calls], "Blocked.")


def test_transcript_extractor_keeps_direct_assistant_calls_in_order():
    """Dropping a direct call would make the stored transcript incomplete."""
    messages = [{
        "role": "assistant",
        "tool_calls": [
            {"function": {"name": "skill_view", "arguments": '{"name":"gitlab"}'}},
            {"function": {"name": "read_file", "arguments": '{"path":"/tmp/x"}'}},
            {"function": {"name": "tool_describe", "arguments": '{"names":["gitlab_read_job"]}'}},
        ],
    }]
    assert [call["name"] for call in runner.extract_gitlab_transcript(messages)] == [
        "skill_view", "read_file", "tool_describe",
    ]


@pytest.mark.parametrize(
    ("claude", "openai"),
    [
        ("claude-3-7-sonnet", "openai/gpt-4.1"),
        ("anthropic/claude-3-7-sonnet", "gpt-4.1"),
    ],
)
def test_model_validation_requires_explicit_provider_namespaces(claude, openai):
    """Alias-only model identifiers must not select an ambiguous provider."""
    with pytest.raises(ValueError, match="explicit"):
        runner.validate_model_pair(claude, openai)


def test_model_validation_accepts_distinct_explicit_provider_namespaces():
    """Configured provider-qualified Claude and GPT IDs are accepted."""
    runner.validate_model_pair("anthropic/claude-sonnet-4", "openai/gpt-5")


def test_output_directory_must_be_ignored_when_inside_worktree(tmp_path, monkeypatch):
    """An in-repo report directory is rejected unless Git ignores it."""
    monkeypatch.setattr(runner, "WORKTREE_ROOT", tmp_path)
    with pytest.raises(ValueError, match="ignored"):
        runner.require_safe_output_dir(tmp_path / "reports")


def test_dispatch_stub_never_calls_real_gitlab_handler():
    """Both read and write GitLab calls are intercepted before networking."""
    invoked = []

    def real_dispatch(name, args, **kwargs):
        invoked.append(name)
        return "network"

    dispatch, failures = runner.gitlab_dispatch_stub(real_dispatch)
    read = json.loads(dispatch("gitlab_list_pipelines", {"project": "x"}))
    write = json.loads(dispatch("gitlab_create_issue", {"project": "x"}))
    assert read["result"] == "fake GitLab read"
    assert write["error"] == "GitLab write blocked by live routing harness"
    assert failures == ["gitlab_create_issue"]
    assert invoked == []
