"""Request-local OTTO v1 headers and response-echo enforcement."""

from __future__ import annotations

from typing import Any, Mapping

from agent.tool_choice_policy import ToolChoicePolicyError, ToolOperationContext


CONTRACT_HEADER = "X-Otto-Tool-Contract"
CALL_ROLE_HEADER = "X-Otto-Call-Role"
CONTRACT_VERSION = "v1"
_OTTO_PROVIDERS = frozenset({"otto", "otto-gateway"})
_HEADER_CAPABLE_MODES = frozenset(
    {"chat_completions", "codex_responses", "anthropic_messages"}
)


class OttoToolContractError(ToolChoicePolicyError):
    """Terminal, content-free OTTO protocol compatibility failure."""

    def __init__(self) -> None:
        super().__init__(
            "otto_tool_contract_unavailable",
            "The selected gateway does not support the requested tool contract.",
        )


def otto_headers(ctx: ToolOperationContext | None) -> dict[str, str]:
    """Generate the exact static OTTO headers from trusted operation context."""
    if ctx is None or ctx.otto_contract_version != CONTRACT_VERSION:
        return {}
    if ctx.call_role not in {"primary", "post_tool"}:
        raise OttoToolContractError()
    return {
        CONTRACT_HEADER: CONTRACT_VERSION,
        CALL_ROLE_HEADER: ctx.call_role,
    }


def add_otto_request_headers(
    kwargs: dict[str, Any],
    ctx: ToolOperationContext | None,
    *,
    provider: str | None,
    api_mode: str | None,
) -> dict[str, Any]:
    """Merge host-owned v1 headers without changing request body fields."""
    owned = otto_headers(ctx)
    if not owned:
        return kwargs
    if (provider or "").strip().lower() not in _OTTO_PROVIDERS:
        raise OttoToolContractError()
    if api_mode not in _HEADER_CAPABLE_MODES:
        raise OttoToolContractError()

    result = dict(kwargs)
    existing = result.get("extra_headers")
    merged = {
        str(key): str(value)
        for key, value in (existing.items() if isinstance(existing, dict) else ())
        if str(key).lower()
        not in {CONTRACT_HEADER.lower(), CALL_ROLE_HEADER.lower()}
    }
    merged.update(owned)
    result["extra_headers"] = merged
    return result


def contract_required(api_kwargs: Mapping[str, Any]) -> bool:
    headers = api_kwargs.get("extra_headers")
    return _header_value(headers, CONTRACT_HEADER) == CONTRACT_VERSION


def verify_response_echo(headers: Any, *, contract_required: bool) -> None:
    if not contract_required:
        return
    if _header_value(headers, CONTRACT_HEADER) != CONTRACT_VERSION:
        raise OttoToolContractError()


def parse_verified_raw_response(raw_response: Any, *, contract_required: bool):
    """Verify before parsing content, and always release the raw response."""
    try:
        verify_response_echo(
            _response_headers(raw_response),
            contract_required=contract_required,
        )
        return raw_response.parse()
    finally:
        _close(raw_response)


def verify_stream_echo(stream: Any, *, contract_required: bool) -> None:
    """Verify a stream before its iterator or any consumer callback is exposed."""
    if not contract_required:
        return
    try:
        response = getattr(stream, "response", None)
        verify_response_echo(
            _response_headers(response),
            contract_required=True,
        )
    except Exception:
        _close(stream)
        raise


def verify_exception_echo(exc: BaseException, *, contract_required: bool) -> None:
    """Require v1 echo on typed HTTP errors while preserving valid errors."""
    if not contract_required:
        return
    response = getattr(exc, "response", None)
    verify_response_echo(
        _response_headers(response),
        contract_required=True,
    )


def _response_headers(response: Any) -> Any:
    if response is None:
        return None
    headers = getattr(response, "headers", None)
    if headers is not None:
        return headers
    http_response = getattr(response, "http_response", None)
    return getattr(http_response, "headers", None)


def _header_value(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        if value is not None:
            return str(value)
    if isinstance(headers, Mapping):
        lowered = name.lower()
        for key, value in headers.items():
            if str(key).lower() == lowered:
                return str(value)
    return None


def _close(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()
        return
    http_response = getattr(value, "http_response", None)
    close = getattr(http_response, "close", None)
    if callable(close):
        close()
