from types import SimpleNamespace

import pytest

from agent.tool_choice_policy import ToolChoicePolicy, ToolOperationContext


def _context(*, role="primary", version="v1"):
    return ToolOperationContext.create(
        ToolChoicePolicy(mode="required"),
        operation_id="operation-fixture",
        call_role=role,
        otto_contract_version=version,
    )


class _RawResponse:
    def __init__(self, headers, parsed="parsed-fixture"):
        self.headers = headers
        self.parsed = parsed
        self.closed = False
        self.parse_calls = 0

    def parse(self):
        self.parse_calls += 1
        return self.parsed

    def close(self):
        self.closed = True


def test_raw_response_tool_contract_headers_are_exact_for_operation_roles():
    from agent.otto_tool_contract import otto_headers

    assert otto_headers(_context()) == {
        "X-Otto-Tool-Contract": "v1",
        "X-Otto-Call-Role": "primary",
    }
    assert otto_headers(_context(role="post_tool")) == {
        "X-Otto-Tool-Contract": "v1",
        "X-Otto-Call-Role": "post_tool",
    }
    assert otto_headers(None) == {}
    assert otto_headers(ToolOperationContext.create()) == {}


def test_raw_response_tool_contract_merges_owned_headers_without_body_changes():
    from agent.otto_tool_contract import add_otto_request_headers

    kwargs = {
        "model": "model-fixture",
        "messages": [{"role": "user", "content": "fixture"}],
        "extra_headers": {
            "X-Existing-Fixture": "preserved",
            "X-Otto-Tool-Contract": "untrusted-value",
        },
    }
    body_before = kwargs["messages"]

    result = add_otto_request_headers(
        kwargs,
        _context(),
        provider="otto",
        api_mode="chat_completions",
    )

    assert result["messages"] is body_before
    assert result["extra_headers"] == {
        "X-Existing-Fixture": "preserved",
        "X-Otto-Tool-Contract": "v1",
        "X-Otto-Call-Role": "primary",
    }


def test_raw_response_tool_contract_direct_provider_has_no_otto_headers():
    from agent.otto_tool_contract import add_otto_request_headers

    result = add_otto_request_headers(
        {"model": "model-fixture"},
        ToolOperationContext.create(ToolChoicePolicy(mode="required")),
        provider="openai",
        api_mode="chat_completions",
    )

    assert "extra_headers" not in result


@pytest.mark.parametrize(
    ("provider", "api_mode"),
    [("openai", "chat_completions"), ("otto", "bedrock_converse")],
)
def test_raw_response_tool_contract_v1_rejects_non_otto_or_uninspectable_route(
    provider, api_mode
):
    from agent.otto_tool_contract import (
        OttoToolContractError,
        add_otto_request_headers,
    )

    with pytest.raises(OttoToolContractError) as raised:
        add_otto_request_headers(
            {"model": "model-fixture"},
            _context(),
            provider=provider,
            api_mode=api_mode,
        )

    assert raised.value.code == "otto_tool_contract_unavailable"


@pytest.mark.parametrize("headers", [{}, {"X-Otto-Tool-Contract": "v2"}])
def test_raw_response_tool_contract_missing_or_wrong_echo_closes_before_parse(headers):
    from agent.otto_tool_contract import (
        OttoToolContractError,
        parse_verified_raw_response,
    )

    raw = _RawResponse(headers)

    with pytest.raises(OttoToolContractError) as raised:
        parse_verified_raw_response(raw, contract_required=True)

    assert raised.value.code == "otto_tool_contract_unavailable"
    assert raw.closed is True
    assert raw.parse_calls == 0


def test_raw_response_tool_contract_exact_echo_parses_then_closes():
    from agent.otto_tool_contract import parse_verified_raw_response

    raw = _RawResponse({"x-otto-tool-contract": "v1"})

    assert parse_verified_raw_response(raw, contract_required=True) == "parsed-fixture"
    assert raw.parse_calls == 1
    assert raw.closed is True


def test_raw_response_tool_contract_transport_uses_raw_interface_before_content(monkeypatch):
    from agent.chat_completion_helpers import _dispatch_nonstreaming_api_request

    raw = _RawResponse({"X-Otto-Tool-Contract": "v1"})
    ordinary_calls = []
    completions = SimpleNamespace(
        create=lambda **kwargs: ordinary_calls.append(kwargs),
        with_raw_response=SimpleNamespace(create=lambda **kwargs: raw),
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    agent = SimpleNamespace(api_mode="chat_completions", provider="otto")
    monkeypatch.setattr(
        "agent.chat_completion_helpers.reserve_provider_transport_attempt",
        lambda *_args, **_kwargs: None,
    )

    result = _dispatch_nonstreaming_api_request(
        agent,
        {
            "model": "model-fixture",
            "extra_headers": {"X-Otto-Tool-Contract": "v1"},
        },
        make_client=lambda *_args, **_kwargs: client,
    )

    assert result == "parsed-fixture"
    assert ordinary_calls == []


def test_raw_response_tool_contract_uninspectable_client_fails_before_dispatch(monkeypatch):
    from agent.chat_completion_helpers import _dispatch_nonstreaming_api_request
    from agent.otto_tool_contract import OttoToolContractError

    ordinary_calls = []
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: ordinary_calls.append(kwargs)
            )
        )
    )
    agent = SimpleNamespace(api_mode="chat_completions", provider="otto")
    monkeypatch.setattr(
        "agent.chat_completion_helpers.reserve_provider_transport_attempt",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(OttoToolContractError):
        _dispatch_nonstreaming_api_request(
            agent,
            {
                "model": "model-fixture",
                "extra_headers": {"X-Otto-Tool-Contract": "v1"},
            },
            make_client=lambda *_args, **_kwargs: client,
        )

    assert ordinary_calls == []


@pytest.mark.parametrize("headers", [{}, {"X-Otto-Tool-Contract": "wrong"}])
def test_raw_response_tool_contract_typed_error_requires_exact_echo(headers):
    from agent.otto_tool_contract import (
        OttoToolContractError,
        verify_exception_echo,
    )

    typed_error = RuntimeError("sanitized typed gateway error")
    typed_error.response = SimpleNamespace(headers=headers)

    with pytest.raises(OttoToolContractError) as raised:
        verify_exception_echo(typed_error, contract_required=True)

    assert raised.value.code == "otto_tool_contract_unavailable"


def test_raw_response_tool_contract_typed_error_with_echo_remains_native():
    from agent.otto_tool_contract import verify_exception_echo

    typed_error = RuntimeError("sanitized typed gateway error")
    typed_error.response = SimpleNamespace(
        headers={"X-Otto-Tool-Contract": "v1"}
    )

    assert verify_exception_echo(typed_error, contract_required=True) is None


def test_echo_before_first_stream_callback_or_iteration():
    from agent.otto_tool_contract import (
        OttoToolContractError,
        verify_stream_echo,
    )

    callbacks = []
    stream = SimpleNamespace(
        response=SimpleNamespace(headers={}),
        close=lambda: callbacks.append("closed"),
    )

    with pytest.raises(OttoToolContractError):
        verify_stream_echo(stream, contract_required=True)

    assert callbacks == ["closed"]
