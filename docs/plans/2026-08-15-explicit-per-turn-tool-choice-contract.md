# Explicit Per-Turn Tool-Choice Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Hermes a request-scoped, explicit tool policy, translate it across supported providers, and coordinate OTTO Gateway v1 without leaking mandatory state into post-tool or auxiliary calls.

**Architecture:** Introduce immutable logical-operation and per-attempt context objects. API/UI controls create the initial context; every request builder receives it explicitly. A valid structured tool call derives a post-tool `auto` context while retaining the operation's OTTO v1 marker. Transport adapters map semantic policy at the last boundary. OTTO responses are accepted only after the exact v1 echo is visible.

**Tech Stack:** Python 3, pytest, OpenAI/Anthropic SDK transports, native Gemini and Bedrock adapters, Hermes conversation loop, aiohttp API server, CLI/TUI/gateway front ends.

## Global Constraints

- Policy originates only from explicit API/UI control, never natural-language parsing.
- The state is per logical operation/per attempt, immutable, and never a mutable agent global.
- No environment or process-wide feature flag.
- `tool_choice` is recomputed after every structured call; post-tool is `auto`.
- System prompt, stable tool definitions, tool ordering, and earlier transcript bytes stay stable.
- No synthetic user message and no broad intent parser.
- Never forward an arbitrary inbound identifier or raw inbound header value.
- Missing/wrong Gateway echo fails before relaying bytes or executing tools.
- Unsupported required/named policy fails closed; never downgrade, switch model/provider, or fall back.
- Title, compression, probes, delegation, retry of unrelated work, and fallback operations start from fresh automatic context.
- Preserve strict role alternation and provider-native tool result carriers.
- Never log prompts, arguments, schemas, connector results, credentials, raw headers, or private project data.

## File and Interface Map

| Concern | Production files | Primary tests |
|---|---|---|
| Policy/context model | new `agent/tool_choice_policy.py` | new `tests/agent/test_tool_choice_policy.py` |
| API/UI opt-in | `gateway/platforms/api_server.py`, `cli.py`, TUI/gateway command files | API, CLI, TUI/gateway command tests |
| Request construction | `agent/chat_completion_helpers.py`, `agent/transports/*.py`, native adapters | transport and builder tests |
| Attempt lifecycle | `agent/conversation_loop.py`, `run_agent.py` binding | conversation-loop tests |
| OTTO headers/echo | new `agent/otto_tool_contract.py`, invocation helpers | new contract transport tests |
| Error behavior | `agent/error_classifier.py`, `agent/conversation_loop.py`, API rendering | classifier, loop, API tests |
| Integration | both repositories and sanitized local Gateway | end-to-end contract tests |

---

### Task 1: Define immutable policy and operation context

**Files:**
- Create: `agent/tool_choice_policy.py`
- Create: `tests/agent/test_tool_choice_policy.py`

- [ ] Write RED tests for omitted/auto, required, none, named OpenAI objects, Anthropic-like names passed internally, unknown names, operation creation, retry copy, post-tool derivation, and terminal clearing.
- [ ] Run RED:

```bash
pytest -q tests/agent/test_tool_choice_policy.py
```

- [ ] Implement frozen types:

```python
@dataclass(frozen=True)
class ToolChoicePolicy:
    mode: Literal["auto", "required", "named", "none"] = "auto"
    name: str | None = None

    def for_post_tool(self) -> "ToolChoicePolicy":
        return ToolChoicePolicy(mode="auto")

@dataclass(frozen=True)
class ToolOperationContext:
    operation_id: str
    policy: ToolChoicePolicy
    call_role: Literal["primary", "post_tool", "title", "compression", "auxiliary"]
    otto_contract_version: Literal["v1"] | None = None

    def after_structured_tool_call(self) -> "ToolOperationContext":
        return replace(self, policy=self.policy.for_post_tool(), call_role="post_tool")
```

Add strict parsers that accept the standard Chat Completions/Responses shapes, validate named tools against an explicit effective catalog, and return stable categories `invalid_tool_choice` or `mandatory_tool_choice_not_supported`.

- [ ] Run GREEN and type/import checks:

```bash
pytest -q tests/agent/test_tool_choice_policy.py
python -m compileall -q agent/tool_choice_policy.py
```

- [ ] Commit:

```bash
git add agent/tool_choice_policy.py tests/agent/test_tool_choice_policy.py
git commit -m "feat: model per-attempt tool choice policy"
```

### Task 2: Add request-scoped API negotiation

**Files:**
- Modify: `gateway/platforms/api_server.py`
- Test: `tests/gateway/test_api_server.py`
- Test: add focused `tests/gateway/test_api_tool_choice_contract.py`

- [ ] Write RED Chat Completions and Responses tests for `required`, named `tool_call`, `auto`, `none`, invalid shape, unknown named tool, missing v1 header, exact inbound v1 opt-in, and no state retained by the next request.
- [ ] Define the API contract: `tool_choice` carries mandatory semantics. Exact inbound `X-Otto-Tool-Contract: v1` requests coordinated Gateway behavior for this logical operation. Parse this static value into trusted context; never copy the raw header, and reject any other nonempty version.
- [ ] Include `tool_choice` and the exact contract version in idempotency fingerprints. Do not include arbitrary headers.
- [ ] Thread `tool_operation_context` through `_run_agent` and the `/v1/runs` executor to `agent.run_conversation`. Validate a named choice after agent creation against the effective stable Hermes tool catalog.
- [ ] Run RED/GREEN:

```bash
pytest -q tests/gateway/test_api_tool_choice_contract.py
pytest -q tests/gateway/test_api_server.py -k 'chat_completions or responses or runs'
```

- [ ] Commit:

```bash
git add gateway/platforms/api_server.py tests/gateway/test_api_tool_choice_contract.py tests/gateway/test_api_server.py
git commit -m "feat: accept explicit per-request tool policy"
```

### Task 3: Add one-shot interactive controls without transcript text

**Files:**
- Create: `agent/tool_choice_control.py`
- Modify: `cli.py`
- Modify: `tui_gateway/methods_prompt.py`, `tui_gateway/slash_worker.py`
- Modify: `gateway/slash_commands.py`, `gateway/run.py`
- Test: new `tests/cli/test_tool_choice_control.py`
- Test: new `tests/tui_gateway/test_tool_choice_control.py`
- Test: new `tests/gateway/test_tool_choice_control.py`

- [ ] Write RED tests for `/tool-choice required`, `/tool-choice named tool_call`, `/tool-choice auto`, and `/tool-choice off`; the state is visible, consumed by exactly one submitted user operation, and cleared on success, cancellation, error, new/reset, and abandoned stream.
- [ ] Implement a one-shot holder owned by the front-end session, not the agent:

```python
@dataclass
class OneShotToolChoice:
    pending: ToolChoicePolicy | None = None
    otto_v1: bool = False

    def consume(self) -> tuple[ToolChoicePolicy, bool]:
        policy = self.pending or ToolChoicePolicy()
        enabled = self.otto_v1
        self.pending = None
        self.otto_v1 = False
        return policy, enabled
```

The control generates metadata passed to `run_conversation`; it never appends a message or edits the system prompt. Use `/tool-choice required --otto-v1` (and the equivalent structured TUI/platform command) to enable coordinated behavior; `auto`/`off` disables it for the next operation.

- [ ] Run GREEN:

```bash
pytest -q tests/cli/test_tool_choice_control.py tests/tui_gateway/test_tool_choice_control.py tests/gateway/test_tool_choice_control.py
```

- [ ] Commit:

```bash
git add agent/tool_choice_control.py cli.py tui_gateway/methods_prompt.py tui_gateway/slash_worker.py gateway/slash_commands.py gateway/run.py tests/cli/test_tool_choice_control.py tests/tui_gateway/test_tool_choice_control.py tests/gateway/test_tool_choice_control.py
git commit -m "feat: add one-shot tool choice controls"
```

### Task 4: Pass policy through the central request builder

**Files:**
- Modify: `agent/chat_completion_helpers.py`
- Modify: `agent/transports/base.py`
- Modify: `agent/transports/chat_completions.py`
- Modify: `agent/transports/anthropic.py`
- Modify: `agent/transports/codex.py`
- Modify: `agent/transports/bedrock.py`
- Modify: `agent/bedrock_adapter.py`
- Test: builder and transport tests under `tests/agent/`

- [ ] Add RED tests proving `build_api_kwargs(..., attempt_context=...)` emits no stale policy when omitted and never mutates `agent.tools`.
- [ ] Change the single builder signature:

```python
def build_api_kwargs(
    agent,
    api_messages: list,
    tools_for_api: list | None = None,
    *,
    attempt_context: ToolOperationContext | None = None,
) -> dict:
```

Pass the normalized policy to every `build_kwargs` call.

- [ ] Map policies at the transport boundary:

| Policy | Chat Completions | Anthropic | Responses | Gemini | Bedrock |
|---|---|---|---|---|---|
| auto | `auto`/omit | `{"type":"auto"}` | `auto` | `AUTO` | omit/auto |
| required | `required` | `{"type":"any"}` | `required` | `ANY` | `{"any":{}}` |
| named | named function | named tool | specific function | `ANY` + allowed name | `{"tool":{"name":...}}` |
| none | `none` | omit tools | `none` | `NONE` | omit tools |

Replace `ResponsesApiTransport`'s hard-coded `auto`. Pass the already-supported Anthropic/Gemini value. Extend `build_converse_kwargs` with `tool_choice` and set `toolConfig.toolChoice`.

- [ ] Add capability validation before dispatch. Required/named with no tools, an unknown name, unsupported model/transport, or incompatible mode raises `mandatory_tool_choice_not_supported`; do not strip the policy or reasoning settings.
- [ ] Run focused tests:

```bash
pytest -q tests/agent/test_chat_completion_helpers.py tests/agent/test_transports.py tests/agent/test_anthropic_adapter.py tests/agent/test_gemini_native_adapter.py tests/agent/test_bedrock_adapter.py -k 'tool_choice or tool_policy'
```

- [ ] Commit:

```bash
git add agent/chat_completion_helpers.py agent/transports agent/bedrock_adapter.py tests/agent
git commit -m "feat: map tool policy across provider transports"
```

### Task 5: Generate OTTO headers and verify the echo before bytes or tools

**Files:**
- Create: `agent/otto_tool_contract.py`
- Create: `tests/agent/test_otto_tool_contract.py`
- Modify: `agent/chat_completion_helpers.py`
- Modify: `agent/transports/chat_completions.py`, `anthropic.py`, `codex.py`
- Test: transport invocation/streaming tests

- [ ] Write RED tests for exact outbound headers on primary/post-tool attempts, no headers for direct providers or auxiliary calls, missing/wrong echo, echo on typed errors, non-streaming validation, and streaming validation before the first callback.
- [ ] Build headers only from immutable context:

```python
def otto_headers(ctx: ToolOperationContext | None) -> dict[str, str]:
    if ctx is None or ctx.otto_contract_version != "v1":
        return {}
    return {
        "X-Otto-Tool-Contract": "v1",
        "X-Otto-Call-Role": ctx.call_role,
    }
```

Merge through `extra_headers` without changing body, prompt cache key, or stable prefix.

- [ ] For OpenAI/compatible SDKs use the raw-response interface so headers are available before parsing/iterating. For streaming, enter the SDK streaming/raw response, verify exact echo, then expose the iterator. Apply the equivalent raw-response seam to Anthropic if an OTTO route uses Messages. Native direct Gemini/Bedrock never receive OTTO headers.
- [ ] Raise terminal `otto_tool_contract_unavailable` on missing/wrong echo, close the response, emit no callback delta, and return no tool call. Never retry or fall back.
- [ ] Do not recognize a route by prompt/model text. The operation explicitly requests v1 and the selected transport must be an HTTP surface capable of the header/echo contract; otherwise fail before dispatch.
- [ ] Run GREEN:

```bash
pytest -q tests/agent/test_otto_tool_contract.py tests/agent -k 'raw_response and tool_contract or echo_before'
```

- [ ] Commit:

```bash
git add agent/otto_tool_contract.py agent/chat_completion_helpers.py agent/transports tests/agent/test_otto_tool_contract.py
git commit -m "feat: enforce otto tool contract echo"
```

### Task 6: Implement attempt lifecycle in the conversation loop

**Files:**
- Modify: `agent/conversation_loop.py`
- Modify: `run_agent.py`
- Test: new `tests/agent/test_tool_choice_lifecycle.py`
- Test: relevant conversation-loop tests

- [ ] Write RED tests for initial required/named request, same-attempt network retry, structured tool call transition, post-tool auto, final clear, cancellation, maximum iterations, provider error, fallback rejection, and concurrent operations on one cached agent.
- [ ] Add `tool_operation_context=None` to `run_conversation`. Create a local `current_attempt_context`; pass it explicitly at both `_build_api_kwargs` call sites.
- [ ] On a valid normalized structured tool call, derive `current_attempt_context.after_structured_tool_call()` before executing tools. That retains v1/operation ID and changes the call role/policy to `post_tool`/`auto`.
- [ ] A retry of the same network attempt deliberately reuses the same local context. A provider fallback may translate the same semantic policy only when explicitly allowed and supported; Gateway protocol/echo errors are terminal and never activate fallback.
- [ ] Clear local references in all terminal paths. Do not store them on `agent`, in conversation messages, SessionDB, the system prompt, or tool definitions.
- [ ] Run GREEN and concurrency tests:

```bash
pytest -q tests/agent/test_tool_choice_lifecycle.py
pytest -q tests/agent -k 'conversation_loop and (tool or concurrent or retry or fallback)'
```

- [ ] Commit:

```bash
git add agent/conversation_loop.py run_agent.py tests/agent/test_tool_choice_lifecycle.py
git commit -m "feat: scope mandatory tool policy to one operation"
```

### Task 7: Make auxiliary calls and caching provably isolated

**Files:**
- Modify only if tests expose a missing explicit default: `agent/title_generator.py`, `agent/conversation_compression.py`, auxiliary request helpers
- Test: title, compression, prompt-cache, fallback, retry, and middleware tests

- [ ] Add RED tests that title generation, compression, summaries, probes, delegated work, middleware retries for unrelated requests, and fallback initialization construct fresh `auto`/no-v1 contexts.
- [ ] Assert the initial and post-tool requests reuse the identical system prompt and stable ordered tool list. Assert `tool_choice`, contract header, role header, and operation ID do not participate in existing static prompt-cache key derivation.
- [ ] Add explicit `attempt_context=None` only at auxiliary call sites that otherwise inherit caller state; do not rewrite prompts or tools.
- [ ] Run GREEN:

```bash
pytest -q tests/agent -k 'title or compression or prompt_cache or auxiliary or fallback'
pytest -q tests/hermes_cli -k 'title or compression'
```

- [ ] Commit:

```bash
git add agent tests/agent tests/hermes_cli
git commit -m "test: isolate tool policy from auxiliary requests"
```

### Task 8: Classify Gateway contract errors as terminal and render them natively

**Files:**
- Modify: `agent/error_classifier.py`
- Modify: `agent/conversation_loop.py`
- Modify: `gateway/platforms/api_server.py`
- Test: `tests/agent/test_error_classifier.py`, conversation-loop tests, API tests

- [ ] Add RED cases for `unsupported_tool_contract_version`, `mandatory_tool_choice_not_supported`, `otto_tool_contract_unavailable`, `selected_model_tool_protocol_failed`, and `selected_model_tool_result_provenance_failed` at HTTP 400/502.
- [ ] Extract only an allowlisted structured error code from SDK exceptions. Classify all five as terminal/no retry/no fallback before generic 500/502 logic.
- [ ] Preserve the safe code/message in OpenAI Chat Completions and Responses error envelopes. Streaming must emit one terminal protocol-native error before any assistant delta. Never include Gateway/Kiro internals, response text, schemas, or arguments.
- [ ] Run GREEN:

```bash
pytest -q tests/agent/test_error_classifier.py tests/gateway/test_api_tool_choice_contract.py -k 'protocol or contract or mandatory'
pytest -q tests/agent -k 'selected_model_tool or no_fallback'
```

- [ ] Commit:

```bash
git add agent/error_classifier.py agent/conversation_loop.py gateway/platforms/api_server.py tests/agent tests/gateway/test_api_tool_choice_contract.py
git commit -m "feat: surface tool contract failures without fallback"
```

### Task 9: Add bounded observability

**Files:**
- Create or modify the existing request telemetry helper selected during implementation
- Modify: `agent/conversation_loop.py`
- Test: telemetry tests

- [ ] Add RED tests that diagnostics contain only operation correlation hash/ID, allowlisted role, requested model/explicit-auto status, policy, contract version, transport, echo boolean, structured-call boolean, post-tool boolean, allowlisted terminal code, and retry/fallback decision.
- [ ] Ensure arbitrary model text, prompts, arguments, schemas, tool results, credentials, raw headers, and session IDs cannot enter the event.
- [ ] Send `X-Otto-Call-Role` for diagnostics, but prove Gateway behavior is identical if it is absent, invalid, or changed.
- [ ] Run GREEN and privacy scans:

```bash
pytest -q tests/agent -k 'telemetry and tool_contract'
rg -n 'example-group|deferred_list_projects|Authorization:|Bearer ' tests/agent tests/gateway
```

- [ ] Commit:

```bash
git add agent tests/agent
git commit -m "feat: observe bounded tool contract outcomes"
```

### Task 10: Run coordinated integration and full Hermes verification

**Files:**
- Create: `docs/verification/2026-08-15-explicit-tool-contract-integration.md`
- Add: sanitized integration fixture/tests in the existing API/transport test locations

- [ ] Against a deployed Gateway v1 build, run the sanitized original bug shape through Hermes with an explicit model and `required`: verify outbound required + v1, no direct execution of narrated hidden wrappers, one Gateway correction, one Hermes tool execution, post-tool auto + v1, and final prose.
- [ ] Repeat named outer `tool_call`, optional documentation prose, auto model, normal post-tool prose, provenance refusal, failed correction, missing echo, cancellation, timeout, and streaming/non-streaming cases.
- [ ] Verify OpenAI Chat Completions, Responses, Anthropic, Gemini, and Bedrock mapping with transport fakes; verify actual OTTO integration only on the configured supported HTTP surface(s). Record unsupported combinations as typed failures, not silent downgrade.
- [ ] Confirm no mandatory state in title, compression, auxiliary, retry, or fallback captures and no prompt-cache/stable-prefix changes.
- [ ] Run focused then full verification:

```bash
pytest -q tests/agent/test_tool_choice_policy.py tests/agent/test_tool_choice_lifecycle.py tests/agent/test_otto_tool_contract.py tests/gateway/test_api_tool_choice_contract.py
pytest -q
python -m compileall -q agent gateway hermes_cli tui_gateway
git diff --check
git status --short
```

- [ ] Commit only the sanitized verification artifact/test additions:

```bash
git add docs/verification/2026-08-15-explicit-tool-contract-integration.md tests
git commit -m "test: verify coordinated explicit tool contract"
```

## Cross-Repository Ordering

1. Implement, verify, and deploy the Gateway plan first.
2. Directly probe v1 echo and typed errors without Hermes.
3. Implement Hermes policy/mappings while keeping user controls unavailable.
4. Verify Hermes echo handling against deployed Gateway v1.
5. Enable API/UI request-scoped controls.
6. Roll back by omitting `tool_choice` mandatory selection and v1 on new operations; no environment change is needed.

## Final Review Checklist

- [ ] Every approved Hermes acceptance scenario maps to a test.
- [ ] `tool_choice` is explicit per attempt and becomes auto immediately after a structured call.
- [ ] Exact v1 is request-scoped and no environment flag exists.
- [ ] Missing echo stops bytes and tool execution.
- [ ] Direct-provider mappings work or fail with the typed unsupported category.
- [ ] No policy enters system text, transcript, SessionDB, stable tools, or cache keys.
- [ ] Auxiliary/concurrent calls cannot observe stale state.
- [ ] Full pytest, compile, diff, and privacy checks pass.
