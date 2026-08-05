"""Tests for the transport ABC, registry, and AnthropicTransport."""

import pytest
from types import SimpleNamespace

from agent.transports.base import ProviderTransport
from agent.transports.types import NormalizedResponse
from agent.transports import get_transport, register_transport, _REGISTRY
from agent.structured_output import (
    StructuredOutputRequest,
    StructuredOutputStrategy,
    normalize_schema,
)


# ── ABC contract tests ──────────────────────────────────────────────────

class TestProviderTransportABC:
    """Verify the ABC contract is enforceable."""

    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            ProviderTransport()

    def test_concrete_must_implement_all_abstract(self):
        class Incomplete(ProviderTransport):
            @property
            def api_mode(self):
                return "test"
        with pytest.raises(TypeError):
            Incomplete()

    def test_minimal_concrete(self):
        class Minimal(ProviderTransport):
            @property
            def api_mode(self):
                return "test_minimal"
            def convert_messages(self, messages, **kw):
                return messages
            def convert_tools(self, tools):
                return tools
            def build_kwargs(self, model, messages, tools=None, **params):
                return {"model": model, "messages": messages}
            def normalize_response(self, response, **kw):
                return NormalizedResponse(content="ok", tool_calls=None, finish_reason="stop")

        t = Minimal()
        assert t.api_mode == "test_minimal"
        assert t.validate_response(None) is True  # default
        assert t.extract_cache_stats(None) is None  # default
        assert t.map_finish_reason("end_turn") == "end_turn"  # default passthrough


# ── Registry tests ───────────────────────────────────────────────────────

class TestTransportRegistry:

    def test_get_unregistered_returns_none(self):
        assert get_transport("nonexistent_mode") is None



    def test_register_and_get(self):
        class DummyTransport(ProviderTransport):
            @property
            def api_mode(self):
                return "dummy_test"
            def convert_messages(self, messages, **kw):
                return messages
            def convert_tools(self, tools):
                return tools
            def build_kwargs(self, model, messages, tools=None, **params):
                return {}
            def normalize_response(self, response, **kw):
                return NormalizedResponse(content=None, tool_calls=None, finish_reason="stop")

        register_transport("dummy_test", DummyTransport)
        t = get_transport("dummy_test")
        assert t.api_mode == "dummy_test"
        # Cleanup
        _REGISTRY.pop("dummy_test", None)


# ── AnthropicTransport tests ────────────────────────────────────────────

class TestAnthropicTransport:

    @pytest.fixture
    def transport(self):
        import agent.transports.anthropic  # noqa: F401
        return get_transport("anthropic_messages")


    def test_convert_tools_simple(self, transport):
        tools = [{
            "type": "function",
            "function": {
                "name": "test_tool",
                "description": "A test",
                "parameters": {"type": "object", "properties": {}},
            }
        }]
        result = transport.convert_tools(tools)
        assert len(result) == 1
        assert result[0]["name"] == "test_tool"
        assert "input_schema" in result[0]







    def test_map_finish_reason(self, transport):
        assert transport.map_finish_reason("end_turn") == "stop"
        assert transport.map_finish_reason("tool_use") == "tool_calls"
        assert transport.map_finish_reason("max_tokens") == "length"
        assert transport.map_finish_reason("stop_sequence") == "stop"
        assert transport.map_finish_reason("refusal") == "content_filter"
        assert transport.map_finish_reason("model_context_window_exceeded") == "length"
        assert transport.map_finish_reason("unknown") == "stop"




    def test_normalize_response_text(self, transport):
        """Test normalization of a simple text response."""
        r = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="Hello world")],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            model="claude-sonnet-4-6",
        )
        nr = transport.normalize_response(r)
        assert isinstance(nr, NormalizedResponse)
        assert nr.content == "Hello world"
        assert nr.tool_calls is None or nr.tool_calls == []
        assert nr.finish_reason == "stop"

    def test_normalize_response_tool_calls(self, transport):
        """Test normalization of a tool-use response."""
        r = SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="tool_use",
                    id="toolu_123",
                    name="terminal",
                    input={"command": "ls"},
                ),
            ],
            stop_reason="tool_use",
            usage=SimpleNamespace(input_tokens=10, output_tokens=20),
            model="claude-sonnet-4-6",
        )
        nr = transport.normalize_response(r)
        assert nr.finish_reason == "tool_calls"
        assert len(nr.tool_calls) == 1
        tc = nr.tool_calls[0]
        assert tc.name == "terminal"
        assert tc.id == "toolu_123"
        assert '"command"' in tc.arguments



    def test_build_kwargs_forwards_native_structured_output(self, transport):
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        }
        request = StructuredOutputRequest(
            schema=normalize_schema(schema),
            strategy=StructuredOutputStrategy.NATIVE_JSON_SCHEMA,
            adapter_version=1,
        )

        kwargs = transport.build_kwargs(
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "Return an answer"}],
            max_tokens=1024,
            provider_name="anthropic",
            base_url="https://api.anthropic.com",
            structured_output=request,
        )

        assert kwargs["output_config"] == {
            "format": {"type": "json_schema", "schema": schema}
        }

    @pytest.mark.parametrize(
        "strategy",
        [
            StructuredOutputStrategy.PROMPT_JSON_SCHEMA,
            StructuredOutputStrategy.NATIVE_JSON_MODE,
            StructuredOutputStrategy.UNSUPPORTED,
        ],
    )
    def test_request_overrides_cannot_inject_anthropic_output_format(
        self, transport, strategy
    ):
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        }
        request = StructuredOutputRequest(
            schema=normalize_schema(schema),
            strategy=strategy,
            adapter_version=1,
        )

        kwargs = transport.build_kwargs(
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "Return an answer"}],
            max_tokens=1024,
            provider_name="anthropic",
            base_url="https://api.anthropic.com",
            request_overrides={
                "output_config": {
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": {"type": "string"}},
                }
            },
            structured_output=request,
        )

        assert "format" not in kwargs.get("output_config", {})

    def test_native_anthropic_format_ignores_shadow_override(self, transport):
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        }
        request = StructuredOutputRequest(
            schema=normalize_schema(schema),
            strategy=StructuredOutputStrategy.NATIVE_JSON_SCHEMA,
            adapter_version=1,
        )

        kwargs = transport.build_kwargs(
            model="claude-opus-4-6",
            messages=[{"role": "user", "content": "Return an answer"}],
            max_tokens=1024,
            reasoning_config={"enabled": True, "effort": "high"},
            provider_name="anthropic",
            base_url="https://api.anthropic.com",
            request_overrides={
                "output_config": {
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": {"type": "string"}},
                }
            },
            structured_output=request,
        )

        assert kwargs["output_config"] == {
            "effort": "high",
            "format": {"type": "json_schema", "schema": schema},
        }

    def test_convert_messages_extracts_system(self, transport):
        """Test convert_messages separates system from messages."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
        system, msgs = transport.convert_messages(messages)
        # System should be extracted
        assert system is not None
        # Messages should only have user
        assert len(msgs) >= 1
