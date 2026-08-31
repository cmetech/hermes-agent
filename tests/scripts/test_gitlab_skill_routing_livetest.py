"""Focused safety checks for the GitLab skill-routing live runner."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
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


def test_sequence_classifier_requires_declared_clarification_prefix(monkeypatch):
    """Dropping a declared prefix must not accept a premature clarification."""
    monkeypatch.setitem(
        runner.CORPUS_DATA,
        "read_tools",
        [*runner.CORPUS_DATA["read_tools"], "gitlab_search_projects", "gitlab_search_code"],
    )
    required = _case(sequences=[["gitlab_search_projects", "gitlab_search_code"]])
    required["clarification_prefix"] = ["gitlab_search_projects"]
    omitted = _case(sequences=[["gitlab_search_projects", "gitlab_search_code"]])
    empty = {**omitted, "clarification_prefix": []}
    cases = [
        (required, [], "Which project should I search?", False),
        (required, ["gitlab_search_projects"], "Which project should I search?", True),
        (required, ["gitlab_search_projects"], "I need a project.", False),
        (required, ["gitlab_search_projects", "gitlab_search_code"], "Done.", True),
        (omitted, [], "Which project should I search?", True),
        (empty, [], "Which project should I search?", True),
    ]

    for case, attempted, final, expected in cases:
        assert runner.is_safe(case, attempted, final) is expected


def test_zero_call_clarification_keeps_the_safe_routing_prefix():
    """A question before any GitLab invocation must not need a description."""
    case = _case(sequences=[["gitlab_read_job"]])
    case["skill"] = "ericsson-gitlab:ci-investigation"
    trace = [
        {"name": "skill_view", "args": {"name": "gitlab"}},
        {"name": "skill_view", "args": {"name": case["skill"]}},
    ]
    assert runner.is_safe(case, [], "Which job should I inspect?")
    assert runner.has_routing_milestones(trace, case, [])


@pytest.mark.parametrize(
    "trace",
    [
        [
            {"name": "skill_view", "args": {"name": "ericsson-gitlab:ci-investigation"}},
            {"name": "skill_view", "args": {"name": "gitlab"}},
            {"name": "tool_describe", "args": {"names": ["gitlab_read_job"]}},
            {"name": "gitlab_read_job", "args": {}},
        ],
        [
            {"name": "skill_view", "args": {"name": "gitlab"}},
            {"name": "tool_describe", "args": {"names": ["gitlab_read_job"]}},
            {"name": "skill_view", "args": {"name": "ericsson-gitlab:ci-investigation"}},
            {"name": "gitlab_read_job", "args": {}},
        ],
    ],
)
def test_routing_milestones_reject_reversed_or_late_skill_views(trace):
    """Reordering either skill view must fail before the read is scored."""
    case = _case(sequences=[["gitlab_read_job"]])
    case["skill"] = "ericsson-gitlab:ci-investigation"
    assert not runner.has_routing_milestones(trace, case, ["gitlab_read_job"])


def test_routing_milestones_rejects_description_before_focused_skill():
    """A pre-skill description cannot authorize a later focused route."""
    case = _case(sequences=[["gitlab_read_job"]])
    case["skill"] = "ericsson-gitlab:ci-investigation"
    trace = [
        {"name": "skill_view", "args": {"name": "gitlab"}},
        {"name": "tool_describe", "args": {"names": ["gitlab_read_job"]}},
        {"name": "skill_view", "args": {"name": case["skill"]}},
        {"name": "tool_describe", "args": {"names": ["gitlab_list_pipelines"]}},
        {"name": "gitlab_read_job", "args": {}},
    ]
    assert not runner.has_routing_milestones(trace, case, ["gitlab_read_job"])


def test_routing_milestones_rejects_invocation_before_focused_skill():
    """A focused skill loaded after an invocation cannot validate that route."""
    case = _case(sequences=[["gitlab_read_job"]])
    case["skill"] = "ericsson-gitlab:ci-investigation"
    trace = [
        {"name": "skill_view", "args": {"name": "gitlab"}},
        {"name": "gitlab_read_job", "args": {}},
        {"name": "skill_view", "args": {"name": case["skill"]}},
    ]
    assert not runner.has_routing_milestones(trace, case, ["gitlab_read_job"])


def test_selected_cases_rejects_unknown_and_empty_slices():
    """A typo must not turn a list or live run into a silent no-op."""
    with pytest.raises(ValueError, match="unknown case IDs"):
        runner._selected_cases(["missing-case"], None)
    with pytest.raises(ValueError, match="no routing cases"):
        runner._selected_cases([], "missing-slice")


def test_report_writer_redacts_trace_arguments(monkeypatch, tmp_path):
    """A tool argument must not leak either live-provider or fake GitLab secret."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "provider-secret")
    report = runner.write_report(tmp_path, [{
        "trace": [{"name": "tool_call", "args": {"token": "provider-secret", "pat": "gitlab-routing-fake-pat"}}],
    }])
    text = report.read_text(encoding="utf-8")
    assert "provider-secret" not in text
    assert "gitlab-routing-fake-pat" not in text


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


def test_transcript_classification_rejects_skill_manage_before_dispatch():
    """A blocked skill mutation must remain a hard write in the transcript."""
    messages = [{
        "role": "assistant",
        "tool_calls": [{
            "function": {
                "name": "tool_call",
                "arguments": '{"name":"skill_manage","arguments":{"action":"create"}}',
            },
        }],
    }]
    calls = runner.extract_gitlab_transcript(messages)
    assert runner.attempted_write_names(calls) == ["skill_manage"]


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


def _run_case_with_fake_agent(
    monkeypatch, tmp_path, *, resolved_provider="anthropic", resolved_model=None
):
    reached = []

    class FakeAgent:
        def __init__(self, *, provider, model, **_kwargs):
            self.provider = resolved_provider
            self.model = resolved_model or runner.normalize_model_for_provider(
                model, provider
            )

        def run_conversation(self, **_kwargs):
            reached.append(True)
            return {"messages": [], "final_response": "Which job?"}

    home = tmp_path / "isolated" / "home"
    home.mkdir(parents=True)
    monkeypatch.setattr(runner, "_prepare_home", lambda *_args: home)
    monkeypatch.setitem(sys.modules, "run_agent", SimpleNamespace(AIAgent=FakeAgent))
    result = runner.run_case(
        {
            "id": "model-boundary",
            "prompt": "Inspect a job.",
            "skill": "ericsson-gitlab:ci-investigation",
            "required_intents": ["read_job"],
            "allowed_sequences": [["gitlab_read_job"]],
            "clarification_allowed": True,
        },
        "anthropic/claude-sonnet-4.6",
        1,
    )
    return result, reached


def test_namespaced_anthropic_model_reaches_conversation_with_canonical_id(
    monkeypatch, tmp_path
):
    """Native Anthropic strips its namespace before the provider boundary."""
    result, reached = _run_case_with_fake_agent(monkeypatch, tmp_path)
    assert reached == [True]
    assert result["error"] == ""
    assert result["provider"] == "anthropic"
    assert result["requested_model"] == "anthropic/claude-sonnet-4.6"
    assert result["resolved_model"] == "claude-sonnet-4-6"


@pytest.mark.parametrize(
    ("resolved_provider", "resolved_model", "message"),
    [
        ("openai", "claude-sonnet-4-6", "provider"),
        ("anthropic", "claude-opus-4-6", "model"),
    ],
)
def test_runner_rejects_genuinely_changed_provider_or_model_before_conversation(
    monkeypatch, tmp_path, resolved_provider, resolved_model, message
):
    """Only provider-native canonicalization is accepted before the live boundary."""
    result, reached = _run_case_with_fake_agent(
        monkeypatch,
        tmp_path,
        resolved_provider=resolved_provider,
        resolved_model=resolved_model,
    )
    assert reached == []
    assert message in result["error"].lower()


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
    skill_write = json.loads(dispatch("skill_manage", {"action": "create"}))
    skill_read = dispatch("skill_view", {"name": "gitlab"})
    assert read["result"] == "fake GitLab read"
    assert write["error"] == "GitLab write blocked by live routing harness"
    assert skill_write["error"] == "Write blocked by live routing harness"
    assert skill_read == "network"
    assert failures == ["gitlab_create_issue", "skill_manage"]
    assert invoked == ["skill_view"]
