from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.anthropic_adapter import create_anthropic_message
from agent.chat_completion_helpers import _dispatch_nonstreaming_api_request
from agent.codex_runtime import run_codex_app_server_turn, run_codex_stream
from agent.provider_attempts import (
    poison_provider_transport_attempt,
    reserve_provider_transport_attempt,
    settle_provider_transport_attempt,
)
from run_agent import AIAgent, ProviderCapabilityDriftError


ROOT = Path(__file__).parents[2]


def _sealed_route_constraint(
    *,
    provider: str,
    model: str,
    api_mode: str,
    base_url: str,
):
    from hermes_cli import runtime_provider as rp

    identity = rp.execution_runtime_identity(
        rp.classify_execution_runtime(
            provider=provider,
            model_config={"provider": provider, "default": model},
            provider_config={
                "api_mode": api_mode,
                "base_url": base_url,
            },
        )
    )
    return rp.CredentialFreeExecutionRouteConstraint(
        route_fingerprint="f" * 64,
        requested_provider=provider,
        model=model,
        api_mode=api_mode,
        base_url=base_url,
        provider_config=(
            {"region": "us-west-2"} if provider == "bedrock" else {}
        ),
        identity=identity,
    )


def _sealed_agent(
    *,
    provider: str,
    model: str,
    api_mode: str,
    base_url: str,
):
    agent = object.__new__(AIAgent)
    agent.provider = provider
    agent.requested_provider = provider
    agent.model = model
    agent.api_mode = api_mode
    agent.base_url = base_url
    agent._client_kwargs = (
        {} if api_mode == "bedrock_converse" else {"base_url": base_url}
    )
    agent._execution_route_constraint = _sealed_route_constraint(
        provider=provider,
        model=model,
        api_mode=api_mode,
        base_url=base_url,
    )
    return agent


def test_chat_completion_reserves_before_transport() -> None:
    events: list[str] = []

    def create(**_kwargs):
        events.append("transport")
        return "response"

    agent = SimpleNamespace(
        api_mode="chat_completions",
        provider="openrouter",
        _provider_attempt_reservation_callback=lambda: events.append("reserve"),
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    result = _dispatch_nonstreaming_api_request(
        agent,
        {"model": "test"},
        make_client=lambda *_args, **_kwargs: client,
    )

    assert result == "response"
    assert events == ["reserve", "transport"]


def test_anthropic_stream_fallback_reserves_each_transport() -> None:
    events: list[str] = []

    class Messages:
        def stream(self, **_kwargs):
            events.append("stream")
            raise RuntimeError("stream not supported")

        def create(self, **_kwargs):
            events.append("create")
            return "response"

    result = create_anthropic_message(
        SimpleNamespace(messages=Messages()),
        {"model": "test"},
        before_transport=lambda: events.append("reserve"),
    )

    assert result == "response"
    assert events == ["reserve", "stream", "reserve", "create"]


def test_agent_anthropic_bridge_installs_the_reservation_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def fake_create(_client, _kwargs, **options):
        options["before_transport"]()
        events.append("transport")
        return "response"

    monkeypatch.setattr(
        "agent.anthropic_adapter.create_anthropic_message", fake_create
    )
    agent = SimpleNamespace(
        api_mode="anthropic_messages",
        _anthropic_client=object(),
        _disable_streaming=False,
        log_prefix="",
        _provider_attempt_reservation_callback=lambda: events.append("reserve"),
        _try_refresh_anthropic_client_credentials=lambda: None,
        _capture_anthropic_response_headers=lambda *_a, **_k: None,
    )

    result = AIAgent._anthropic_messages_create(agent, {"model": "test"})

    assert result == "response"
    assert events == ["reserve", "transport"]


def test_codex_responses_reserves_before_transport() -> None:
    events: list[str] = []
    response = SimpleNamespace(output=[])

    def create(**_kwargs):
        events.append("transport")
        return response

    agent = SimpleNamespace(
        _interrupt_requested=False,
        _provider_attempt_reservation_callback=lambda: events.append("reserve"),
        responses=SimpleNamespace(create=create),
    )
    client = SimpleNamespace(responses=agent.responses)

    assert run_codex_stream(agent, {"model": "test"}, client=client) is response
    assert events == ["reserve", "transport"]


def test_codex_app_server_reserves_before_transport() -> None:
    events: list[str] = []

    class Session:
        def run_turn(self, **_kwargs):
            events.append("transport")
            raise RuntimeError("stop after launch")

        def close(self):
            return None

    agent = SimpleNamespace(
        _codex_session=Session(),
        _interrupt_requested=False,
        _provider_attempt_reservation_callback=lambda: events.append("reserve"),
    )

    result = run_codex_app_server_turn(
        agent,
        user_message="hello",
        original_user_message="hello",
        messages=[{"role": "user", "content": "hello"}],
        effective_task_id="task",
    )

    assert result["completed"] is False
    assert result["error"] == "stop after launch"
    assert events == ["reserve", "transport"]


def _call_counts(path: str, call_name: str) -> dict[str, int]:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != call_name:
            continue
        parent: ast.AST | None = node
        while parent is not None and not isinstance(
            parent, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            parent = parents.get(parent)
        assert isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef))
        counts[parent.name] = counts.get(parent.name, 0) + 1
    return counts


def test_all_provider_transport_launch_sites_remain_reserved() -> None:
    """Fail the merge gate if upstream reconciliation drops a launch seam."""

    assert _call_counts(
        "agent/chat_completion_helpers.py", "reserve_provider_transport_attempt"
    ) == {
        "_dispatch_nonstreaming_api_request": 3,
        # v0.20.0 moved each launch into a per-attempt opener/callback that
        # the managed Relay layer invokes; the reservation moved with it so
        # every physical retry still receives a distinct reservation.
        "_reserved_summary_create": 1,
        "_reserved_summary_retry_create": 1,
        "_open_bedrock_stream": 2,
        "_open_stream": 1,
        "_open_anthropic_stream": 1,
    }
    assert _call_counts("agent/anthropic_adapter.py", "before_transport") == {
        "create_anthropic_message": 2,
    }
    assert _call_counts(
        "agent/codex_runtime.py", "reserve_provider_transport_attempt"
    ) == {
        "run_codex_app_server_turn": 1,
        "_open_codex_stream": 1,
    }
    assert _call_counts(
        "run_agent.py", "reserve_provider_transport_attempt"
    ) == {"_anthropic_messages_create": 1}
    assert _call_counts(
        "agent/conversation_loop.py", "settle_provider_transport_attempt"
    ) == {"run_conversation": 1}
    assert _call_counts(
        "agent/conversation_loop.py", "poison_provider_transport_attempt"
    ) == {"run_conversation": 2}


def test_reservation_helper_is_optional_but_never_swallows_refusal() -> None:
    reserve_provider_transport_attempt(SimpleNamespace())

    def refuse() -> None:
        raise RuntimeError("provider grant exhausted")

    agent = SimpleNamespace(_provider_attempt_reservation_callback=refuse)
    with pytest.raises(RuntimeError, match="provider grant exhausted"):
        reserve_provider_transport_attempt(agent)


def test_sealed_bedrock_transport_pins_actual_boto_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import bedrock_adapter

    endpoint = "https://bedrock-runtime.us-west-2.amazonaws.com"
    agent = _sealed_agent(
        provider="bedrock",
        model="amazon.nova-pro-v1:0",
        api_mode="bedrock_converse",
        base_url=endpoint,
    )
    agent._bedrock_region = "us-west-2"
    constructions: list[tuple[str, dict[str, object]]] = []
    converse_calls: list[dict[str, object]] = []

    class FakeBoto3:
        __version__ = "1.40.0"

        @staticmethod
        def client(service: str, **kwargs):
            constructions.append((service, dict(kwargs)))
            actual_endpoint = str(
                kwargs.get("endpoint_url")
                or "https://sdk-default.invalid"
            )
            return SimpleNamespace(
                meta=SimpleNamespace(endpoint_url=actual_endpoint),
                converse=lambda **call_kwargs: (
                    converse_calls.append(dict(call_kwargs))
                    or {"output": {"message": {"content": []}}}
                ),
            )

    bedrock_adapter.reset_client_cache()
    monkeypatch.setattr(bedrock_adapter, "_require_boto3", lambda: FakeBoto3)
    monkeypatch.setattr(
        bedrock_adapter,
        "normalize_converse_response",
        lambda response: response,
    )

    result = _dispatch_nonstreaming_api_request(
        agent,
        {
            "__bedrock_region__": "us-west-2",
            "__bedrock_converse__": True,
            "modelId": agent.model,
            "messages": [],
        },
        make_client=lambda *_args, **_kwargs: None,
    )

    assert result == {"output": {"message": {"content": []}}}
    assert constructions == [
        (
            "bedrock-runtime",
            {"region_name": "us-west-2", "endpoint_url": endpoint},
        )
    ]
    assert converse_calls == [{"modelId": agent.model, "messages": []}]


def test_sealed_bedrock_transport_rejects_actual_endpoint_disagreement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import bedrock_adapter

    endpoint = "https://bedrock-runtime.us-west-2.amazonaws.com"
    agent = _sealed_agent(
        provider="bedrock",
        model="amazon.nova-pro-v1:0",
        api_mode="bedrock_converse",
        base_url=endpoint,
    )
    converse_calls: list[dict[str, object]] = []
    client = SimpleNamespace(
        meta=SimpleNamespace(
            endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com"
        ),
        converse=lambda **kwargs: converse_calls.append(dict(kwargs)),
    )
    monkeypatch.setattr(
        bedrock_adapter,
        "_get_bedrock_runtime_client",
        lambda *_args, **_kwargs: client,
    )

    with pytest.raises(ProviderCapabilityDriftError) as caught:
        _dispatch_nonstreaming_api_request(
            agent,
            {
                "__bedrock_region__": "us-west-2",
                "__bedrock_converse__": True,
                "modelId": agent.model,
                "messages": [],
            },
            make_client=lambda *_args, **_kwargs: None,
        )

    assert str(caught.value) == "provider_capability_drift"
    assert converse_calls == []


def test_unsealed_bedrock_transport_retains_sdk_endpoint_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import bedrock_adapter

    constructions: list[tuple[str, dict[str, object]]] = []

    class FakeBoto3:
        __version__ = "1.40.0"

        @staticmethod
        def client(service: str, **kwargs):
            constructions.append((service, dict(kwargs)))
            return SimpleNamespace(
                meta=SimpleNamespace(
                    endpoint_url=(
                        "https://bedrock-runtime.us-west-2.amazonaws.com"
                    )
                ),
                converse=lambda **_kwargs: {"ok": True},
            )

    bedrock_adapter.reset_client_cache()
    monkeypatch.setattr(bedrock_adapter, "_require_boto3", lambda: FakeBoto3)
    monkeypatch.setattr(
        bedrock_adapter,
        "normalize_converse_response",
        lambda response: response,
    )
    agent = SimpleNamespace(api_mode="bedrock_converse", provider="bedrock")

    result = _dispatch_nonstreaming_api_request(
        agent,
        {
            "__bedrock_region__": "us-west-2",
            "__bedrock_converse__": True,
            "modelId": "amazon.nova-pro-v1:0",
            "messages": [],
        },
        make_client=lambda *_args, **_kwargs: None,
    )

    assert result == {"ok": True}
    assert constructions == [
        ("bedrock-runtime", {"region_name": "us-west-2"})
    ]


def test_relay_stream_preserves_terminal_route_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import relay_llm, relay_runtime

    drift = ProviderCapabilityDriftError()

    class Request:
        def __init__(self, headers, content):
            self.headers = headers
            self.content = content

    class LLM:
        async def stream_execute(
            self,
            _name,
            request,
            provider_stream,
            _observe_chunk,
            _finalizer,
            **_kwargs,
        ):
            try:
                async for _chunk in provider_stream(request):
                    pass
            except Exception as exc:
                raise RuntimeError(
                    f"internal error: {type(exc).__name__}: {exc} (retried 3x)"
                ) from None

    class Runtime:
        relay = SimpleNamespace(LLMRequest=Request, llm=LLM())

        @staticmethod
        def managed_execution_enabled():
            return True

        @staticmethod
        async def run_in_session_async(_session, callback, *args, **kwargs):
            return await callback(*args, **kwargs)

    monkeypatch.setattr(
        relay_runtime,
        "resolve_execution_context",
        lambda _session_id: (Runtime(), object(), None),
    )
    monkeypatch.setattr(relay_llm, "_logical_parent", lambda *_a, **_k: None)
    monkeypatch.setattr(relay_llm, "_codec", lambda *_a, **_k: None)
    monkeypatch.setattr(
        relay_llm,
        "_codec_round_trip_request_body",
        lambda *_a, **_k: None,
    )

    with pytest.raises(ProviderCapabilityDriftError) as caught:
        relay_llm.stream(
            {"model": "test-model", "messages": []},
            lambda _request: (_ for _ in ()).throw(drift),
            session_id="session-1",
            name="test-provider",
            model_name="test-model",
            finalizer=dict,
        )

    assert caught.value is drift


def test_cost_lease_precedes_attempt_reservation_and_releases_when_no_transport():
    events: list[object] = []

    def refuse_attempt() -> None:
        events.append("attempt")
        raise RuntimeError("provider grant exhausted")

    agent = SimpleNamespace(
        provider="synthetic",
        model="model",
        _cost_budget_acquire_callback=lambda: events.append("cost") or "lease",
        _cost_budget_release_unstarted_callback=(
            lambda lease: events.append(("release", lease))
        ),
        _provider_attempt_reservation_callback=refuse_attempt,
    )

    with pytest.raises(RuntimeError, match="provider grant exhausted"):
        reserve_provider_transport_attempt(agent)

    assert events == ["cost", "attempt", ("release", "lease")]
    assert getattr(agent, "_active_cost_budget_attempt_id", None) is None


def test_success_and_ambiguous_transport_close_exactly_one_active_cost_lease():
    settled = []
    poisoned = []
    agent = SimpleNamespace(
        _cost_budget_acquire_callback=lambda: "lease",
        _cost_budget_settle_callback=(
            lambda lease, usage: settled.append((lease, usage)) or {"terminal": False}
        ),
        _cost_budget_poison_callback=(
            lambda lease, code: poisoned.append((lease, code))
        ),
    )

    reserve_provider_transport_attempt(agent)
    evidence = settle_provider_transport_attempt(agent, {"cost": "0.1"})
    assert evidence == {"terminal": False}
    assert settled == [("lease", {"cost": "0.1"})]
    assert poison_provider_transport_attempt(agent, "late") is False

    reserve_provider_transport_attempt(agent)
    assert poison_provider_transport_attempt(
        agent, "authoritative_settlement_ambiguous"
    ) is True
    assert poisoned == [("lease", "authoritative_settlement_ambiguous")]
    assert getattr(agent, "_active_cost_budget_attempt_id", None) is None


def test_nested_physical_fallback_poisons_before_a_second_transport_can_reserve():
    acquired = []
    poisoned = []
    agent = SimpleNamespace(
        _cost_budget_acquire_callback=(
            lambda: acquired.append("lease") or "lease"
        ),
        _cost_budget_poison_callback=(
            lambda lease, code: poisoned.append((lease, code))
        ),
    )

    reserve_provider_transport_attempt(agent)
    with pytest.raises(RuntimeError, match="before authoritative settlement"):
        reserve_provider_transport_attempt(agent)

    assert acquired == ["lease"]
    assert poisoned == [("lease", "authoritative_settlement_ambiguous")]
    assert getattr(agent, "_active_cost_budget_attempt_id", None) is None
