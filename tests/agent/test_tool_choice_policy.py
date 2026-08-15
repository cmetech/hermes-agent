from dataclasses import FrozenInstanceError

import pytest


def _policy_module():
    import agent.tool_choice_policy as policy

    return policy


@pytest.mark.parametrize("value", [None, "auto"])
def test_omitted_and_auto_parse_as_automatic(value):
    policy = _policy_module()

    parsed = policy.parse_tool_choice(value, effective_tool_names={"tool_call"})

    assert parsed == policy.ToolChoicePolicy(mode="auto")


def test_required_parses_when_an_effective_tool_is_available():
    policy = _policy_module()

    parsed = policy.parse_tool_choice(
        "required", effective_tool_names={"tool_call"}
    )

    assert parsed == policy.ToolChoicePolicy(mode="required")


def test_none_parses_without_an_effective_tool_catalog():
    policy = _policy_module()

    parsed = policy.parse_tool_choice("none", effective_tool_names=set())

    assert parsed == policy.ToolChoicePolicy(mode="none")


def test_named_openai_function_choice_is_validated_against_effective_catalog():
    policy = _policy_module()

    parsed = policy.parse_tool_choice(
        {
            "type": "function",
            "function": {"name": "tool_call"},
        },
        effective_tool_names={"tool_call"},
    )

    assert parsed == policy.ToolChoicePolicy(mode="named", name="tool_call")


def test_named_anthropic_tool_choice_is_accepted_at_internal_boundary():
    policy = _policy_module()

    parsed = policy.parse_tool_choice(
        {"type": "tool", "name": "tool_call"},
        effective_tool_names={"tool_call"},
    )

    assert parsed == policy.ToolChoicePolicy(mode="named", name="tool_call")


def test_unknown_named_choice_fails_as_unsupported_mandatory_choice():
    policy = _policy_module()

    with pytest.raises(policy.ToolChoicePolicyError) as exc_info:
        policy.parse_tool_choice(
            {
                "type": "function",
                "function": {"name": "unavailable_fixture"},
            },
            effective_tool_names={"tool_call"},
        )

    assert exc_info.value.code == "mandatory_tool_choice_not_supported"


def test_malformed_choice_fails_with_stable_invalid_category():
    policy = _policy_module()

    with pytest.raises(policy.ToolChoicePolicyError) as exc_info:
        policy.parse_tool_choice(
            {"type": "function", "function": {}},
            effective_tool_names={"tool_call"},
        )

    assert exc_info.value.code == "invalid_tool_choice"


def test_required_without_effective_tools_fails_closed():
    policy = _policy_module()

    with pytest.raises(policy.ToolChoicePolicyError) as exc_info:
        policy.parse_tool_choice("required", effective_tool_names=set())

    assert exc_info.value.code == "mandatory_tool_choice_not_supported"


def test_policy_rejects_a_name_for_non_named_mode():
    policy = _policy_module()

    with pytest.raises(policy.ToolChoicePolicyError) as exc_info:
        policy.ToolChoicePolicy(mode="auto", name="tool_call")

    assert exc_info.value.code == "invalid_tool_choice"


def test_operation_context_is_created_with_explicit_trusted_metadata():
    policy = _policy_module()
    selected = policy.ToolChoicePolicy(mode="required")

    context = policy.ToolOperationContext.create(
        selected,
        operation_id="operation-fixture",
        call_role="primary",
        otto_contract_version="v1",
    )

    assert context.operation_id == "operation-fixture"
    assert context.policy == selected
    assert context.call_role == "primary"
    assert context.otto_contract_version == "v1"


def test_operation_context_is_immutable():
    policy = _policy_module()
    context = policy.ToolOperationContext.create(
        policy.ToolChoicePolicy(mode="required"),
        operation_id="operation-fixture",
    )

    with pytest.raises(FrozenInstanceError):
        context.call_role = "post_tool"


def test_same_network_attempt_retry_reuses_immutable_context():
    policy = _policy_module()
    context = policy.ToolOperationContext.create(
        policy.ToolChoicePolicy(mode="required"),
        operation_id="operation-fixture",
        otto_contract_version="v1",
    )

    retry = context.for_retry()

    assert retry is context


def test_structured_tool_call_derives_post_tool_auto_context():
    policy = _policy_module()
    context = policy.ToolOperationContext.create(
        policy.ToolChoicePolicy(mode="named", name="tool_call"),
        operation_id="operation-fixture",
        otto_contract_version="v1",
    )

    post_tool = context.after_structured_tool_call()

    assert post_tool.policy == policy.ToolChoicePolicy(mode="auto")
    assert post_tool.call_role == "post_tool"
    assert post_tool.operation_id == "operation-fixture"
    assert post_tool.otto_contract_version == "v1"


def test_terminal_clearing_returns_no_operation_context():
    policy = _policy_module()
    context = policy.ToolOperationContext.create(
        policy.ToolChoicePolicy(mode="required"),
        operation_id="operation-fixture",
        otto_contract_version="v1",
    )

    assert context.clear() is None
