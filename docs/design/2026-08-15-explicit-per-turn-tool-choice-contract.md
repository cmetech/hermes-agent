# Explicit Per-Turn Tool-Choice Contract

**Date:** 2026-08-15
**Status:** Approved
**Scope:** Hermes as caller and external tool host for OTTO Gateway and direct model-provider transports

## 1. Purpose

This design gives a user or trusted workflow an explicit, structured way to say that the current model attempt must call a tool. It coordinates that intent with OTTO Gateway while preserving Hermes's role as the external tool host.

The intended reader is an engineer implementing or reviewing Hermes request construction, transport translation, conversation-loop lifecycle, and error handling. After reading this document, that engineer should be able to add the contract without parsing arbitrary prompt text, changing the stable toolset, inserting synthetic messages, or leaking mandatory state into later requests.

## 2. Decision

Hermes introduces an immutable per-attempt tool policy:

```text
auto | required | named(<declared tool>) | none
```

The policy is created only from an explicit API or user-interface control. It is never inferred from phrases such as “call exactly once.”

For OTTO Gateway routes:

- An explicitly opted-in logical operation sends `X-Otto-Tool-Contract: v1` on its initial request and any post-tool continuation belonging to that operation.
- The initial mandatory attempt sends native `tool_choice: required` or a named choice.
- Immediately after Hermes receives a valid structured tool call, the next API attempt is recomputed as `auto` unless a separate trusted workflow operation explicitly requires another call.
- Hermes verifies the v1 response echo before accepting response content or executing a surfaced tool call.
- Missing echo yields a safe terminal compatibility error.
- No environment variable or process-wide behavioral flag is added.
- Hermes sends an allowlisted `X-Otto-Call-Role` value for diagnostics only; it is never a behavior or authorization input.

For direct providers, Hermes translates the same internal policy into the provider's native representation and does not send the OTTO-specific header.

## 3. User-facing enablement

### 3.1 API requests

Hermes's OpenAI-compatible Chat Completions and Responses entry points accept the standard `tool_choice` intent for the active Hermes toolset:

```json
"tool_choice": "required"
```

or a named function:

```json
{
  "type": "function",
  "function": {
    "name": "tool_call"
  }
}
```

If the API caller also supplies tools, named selection must resolve against the effective authorized catalog after Hermes applies its normal tool-host policy. An unknown or unavailable name fails before network dispatch.

`auto`, omission, or completion of the logical operation disables mandatory behavior for later turns.

### 3.2 Interactive clients

CLI, TUI, and platform front ends expose a one-shot control that attaches a tool policy to the next submitted user turn. The control is host metadata, not text added to the conversation. It is consumed when request context is created and does not persist as an assistant or user message.

The concrete presentation may differ by client, but every front end must preserve these semantics:

- visible before submission;
- one logical user operation only;
- defaults to `auto`;
- cleared on success, cancellation, terminal error, new conversation, session reset, or explicit disable; and
- never serialized into the stable system prompt or conversation transcript.

This is an explicit command/control parser, not a natural-language intent parser.

## 4. Layman-level behavior

Without explicit selection, Hermes tells the model:

> Here are the tools available to you. Use one if appropriate.

With `required`, Hermes tells the API:

> Your next response must contain a tool call.

After Hermes executes that call, it tells the API:

> The tool result is present. You may now answer normally.

The setting does not make tool output trusted, change the model, or grant access to a connector that Hermes has not authorized.

## 5. Example: initial OTTO Gateway request

The following sanitized request shows the material additions: the standard `tool_choice` field and the versioned contract header.

```http
POST /v1/chat/completions
X-Otto-Tool-Contract: v1
Content-Type: application/json
```

```json
{
  "model": "<explicit-model-id>",
  "stream": true,
  "messages": [
    {
      "role": "user",
      "content": "Use only the connector tools. Call deferred_list_projects exactly once for example-group with recursive true, max_groups 50, and max_projects 100. Then report the requested result fields."
    }
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "tool_search",
        "description": "Find deferred tools",
        "parameters": {"type": "object"}
      }
    },
    {
      "type": "function",
      "function": {
        "name": "tool_describe",
        "description": "Describe a deferred tool",
        "parameters": {"type": "object"}
      }
    },
    {
      "type": "function",
      "function": {
        "name": "tool_call",
        "description": "Execute an authorized deferred tool",
        "parameters": {
          "type": "object",
          "required": ["name", "arguments"]
        }
      }
    }
  ],
  "tool_choice": "required"
}
```

`required` is the normal interactive choice because it permits a bridge discovery call such as `tool_describe` or a direct `tool_call` dispatcher call. A trusted workflow that already possesses the deferred schema may name the outer `tool_call` dispatcher instead.

Hermes's stable system prompt and stable tool prefix remain byte-identical. The policy is a request field, not a prompt mutation.

## 6. Example: post-tool continuation

After Hermes validates and executes the surfaced call, it sends the result using the provider's native structured carrier. The v1 header remains because this request belongs to the same logical operation, but mandatory policy has been cleared:

```http
POST /v1/chat/completions
X-Otto-Tool-Contract: v1
Content-Type: application/json
```

```json
{
  "model": "<same-explicit-model-id>",
  "stream": true,
  "messages": [
    {
      "role": "user",
      "content": "Use only the connector tools. Call deferred_list_projects exactly once with the supplied arguments and report the requested fields."
    },
    {
      "role": "assistant",
      "tool_calls": [
        {
          "id": "call_example",
          "type": "function",
          "function": {
            "name": "tool_call",
            "arguments": "{\"name\":\"deferred_list_projects\",\"arguments\":{\"group\":\"example-group\",\"recursive\":true,\"max_groups\":50,\"max_projects\":100}}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call_example",
      "content": "<untrusted connector result omitted>"
    }
  ],
  "tools": ["<the same stable bridge tool definitions>"],
  "tool_choice": "auto"
}
```

Hermes does not add a synthetic user message. Strict message-role alternation and provider-native tool-result pairing remain unchanged.

## 7. Policy ownership and lifecycle

### 7.1 Immutable attempt context

Tool policy lives in request/turn context, not mutable agent-wide state. Request construction receives it explicitly alongside messages and the stable tool catalog.

This prevents asynchronous title generation, compression, delegated work, and concurrent API requests from observing a stale setting.

### 7.2 State transitions

| Event | Next policy | Contract marker |
|---|---|---|
| Explicit mandatory user turn begins | `required` or named | v1 for an OTTO route |
| Same network attempt is retried | Deliberately copy the same policy | Preserve v1 |
| Authorized fallback for the same logical attempt | Deliberately translate the same policy | Use v1 only when target is OTTO |
| Valid structured tool call received | `auto` for the post-tool continuation | Preserve v1 for the operation |
| Final answer or terminal error | Clear | Clear |
| Optional user turn | `auto` | Absent unless another v1 behavior was explicitly requested |
| Title, compression, summarization, or other auxiliary request | `auto` | Absent |
| New conversation, reset, cancellation, or abandoned stream | Clear | Clear |

Copying policy on a retry of the same logical attempt is intentional propagation, not leakage. Every unrelated request constructs a fresh default policy.

### 7.3 Deferred tools

The effective caller catalog normally contains stable bridge tools such as `tool_search`, `tool_describe`, and `tool_call`. Deferred connector tools need not be declared directly.

- `required` permits any authorized bridge call.
- Named `tool_call` requires the outer dispatcher and is appropriate only when a trusted workflow already knows enough to dispatch.
- Hermes remains responsible for validating the inner name against its scoped deferred catalog and validating arguments before connector execution.

## 8. Transport mapping

Hermes normalizes once and maps at the last transport boundary:

| Internal policy | Chat Completions | Anthropic Messages | Responses/Codex | Gemini | Bedrock Converse | OTTO Gateway |
|---|---|---|---|---|---|---|
| `auto` | `auto` or omit | `{"type":"auto"}` or omit | `auto` | `AUTO` | `auto` or omit | Native surface value |
| `required` | `required` | `{"type":"any"}` | `required` | `ANY` | `any` | Native surface value + v1 header |
| `named(name)` | named `function` | named `tool` | specific/constrained function | `ANY` + allowed name | named `tool` when supported | Native named value + v1 header |
| `none` | `none` | omit tools / native none behavior | `none` | `NONE` | omit tools or supported equivalent | Native surface value |

Current translation support is uneven and must be completed:

- Anthropic has mapping support, but the main builder does not pass per-attempt policy.
- Gemini has native translation support, but ordinary request construction does not supply the policy.
- Responses currently forces `auto` whenever tools are present.
- Bedrock currently builds `toolConfig.tools` without `toolChoice`.
- Chat Completions currently sends tools without `tool_choice`.

Transport or model capability is checked before dispatch. If required or named selection is unsupported, Hermes returns `mandatory_tool_choice_not_supported`. It must not silently use `auto`, remove reasoning settings, change providers, or change models.

Provider-specific incompatibilities, including a reasoning mode that cannot coexist with forced tool selection, use the same fail-closed rule.

Gateway's native Ollama `/api/chat` surface uses a documented v1 `tool_choice` extension because the public Ollama request has no standard semantic carrier. Required or named policy is unsupported on Ollama `/api/generate`, which has no caller-tool catalog; Hermes or a direct client must fail before dispatch rather than relying on prompt text.

## 9. OTTO contract handling

### 9.1 Header ownership

Hermes generates the outbound `X-Otto-Tool-Contract` header from trusted turn context. It does not blindly forward an inbound header supplied to Hermes's public API.

Direct Gateway clients may set the header themselves. Within Hermes, the downstream header is host-owned metadata.

### 9.2 Response echo

Before accepting response content or executing a surfaced tool call, Hermes verifies:

```http
X-Otto-Tool-Contract: v1
```

If the echo is absent or different:

- stop consuming/relaying the response before user-visible content;
- do not execute a tool call from that response;
- return `otto_tool_contract_unavailable`; and
- do not retry against another model or provider.

Streaming transports must perform the echo check when response headers become available, before relaying the first content delta.

### 9.3 Explicit model authority

Gateway errors `selected_model_tool_protocol_failed` and `selected_model_tool_result_provenance_failed` are terminal for the logical operation. Hermes does not retry them as generic HTTP 502 failures and does not activate provider/model fallback.

The safe Gateway code and message are preserved in protocol-native Hermes output. Kiro internals, response text, schemas, arguments, and connector data are never surfaced.

## 10. Tool execution boundary

Hermes remains the only component that executes caller tools in this architecture.

Before execution it continues to:

- resolve the outer call against the stable active tool catalog;
- unwrap `tool_call` only through the dispatcher implementation;
- validate the inner deferred name against the scoped catalog;
- validate required arguments and schema;
- enforce plugin authorization and readiness; and
- return a structured tool result paired to the original call ID.

The v1 header and mandatory policy grant no connector permission by themselves.

## 11. Prompt caching

The contract preserves Hermes's cache invariants:

- The system prompt is byte-stable for the conversation.
- The effective stable tool definitions and order do not change.
- `tool_choice` is an out-of-band request field, not text injected into prior messages.
- The v1 header does not participate in prompt content or cache keys.
- Post-tool continuation reuses the same stable tool catalog.
- No synthetic user message is inserted.

Some providers may reprocess message blocks when native `tool_choice` changes. That provider-defined per-attempt cost is acceptable; Hermes must not invalidate the stable system/tool prefix or rewrite prior context to compensate.

## 12. Auxiliary calls and concurrency

Every request builder receives an explicit call role and explicit tool policy. Allowlisted roles include:

```text
primary | post_tool | correction | title | compression | auxiliary
```

Only `primary` and `post_tool` can participate in an OTTO v1 logical operation. Gateway corrections are internal and are not separate Hermes requests.

Title generation, compression, model probes, summaries, retries for unrelated operations, and background tasks always begin with a fresh automatic policy. Context variables or immutable request objects may carry policy through asynchronous boundaries; mutable global/agent attributes may not.

## 13. Observability

Hermes records bounded, content-free diagnostics:

- logical operation ID;
- call role;
- requested model and explicit/auto mode using existing bounded model attribution;
- internal tool policy;
- OTTO contract version or none;
- target transport;
- response echo present: boolean;
- structured tool call received: boolean;
- post-tool request: boolean;
- terminal Gateway error code when allowlisted; and
- retry/fallback decision.

Hermes never logs prompts, tool arguments, schemas, connector output, credentials, arbitrary provider bodies, raw session identifiers, or internal project listings for this feature.

The call-role signal sent to Gateway is diagnostic only. Gateway must not use it to authorize tool recovery.

For an OTTO route Hermes sends one allowlisted value in:

```http
X-Otto-Call-Role: primary
```

The value is recomputed per request. Title, compression, and other auxiliary calls never inherit `primary` or `post_tool` from a concurrent logical operation.

## 14. Error presentation

Hermes adds stable terminal categories:

| Category | Meaning | Retry/fallback |
|---|---|---|
| `mandatory_tool_choice_not_supported` | Selected transport/model cannot express the requested policy | None |
| `otto_tool_contract_unavailable` | v1 was requested but Gateway did not echo support | None |
| `selected_model_tool_protocol_failed` | Gateway exhausted initial bounded recovery | None |
| `selected_model_tool_result_provenance_failed` | Gateway exhausted post-tool bounded recovery | None |

Messages remain short and safe. The existing generic server-error classifier must recognize these codes before its generic 500/502 retry rule.

## 15. Rollout and compatibility

Deployment order:

1. Gateway v1 support and echo deploy first.
2. Direct probes verify the echo and native error behavior.
3. Hermes transport mappings, immutable policy, echo check, and error classification deploy next.
4. User-facing one-shot controls become available only after the coordinated path passes integration tests.

Requests without explicit per-turn selection remain automatic and omit v1, preserving existing behavior.

Rollback requires no environment change: callers stop selecting mandatory policy, and Hermes stops emitting v1 for new logical operations. In-flight operations retain their immutable context until they finish or are cancelled.

## 16. Acceptance test contract

The implementation plan must include at least:

1. An initial explicit mandatory Chat Completions request emits `required` and v1 on the wire.
2. A named outer dispatcher emits the correct native named shape.
3. Post-tool continuation preserves v1 but emits `auto` or omits mandatory choice.
4. Final-answer completion clears operation policy.
5. Optional turns omit mandatory policy and do not emit v1 unless explicitly requested for another v1 behavior.
6. Title, compression, summary, auxiliary, and model-probe calls never inherit policy.
7. Same-attempt retries deliberately retain policy without agent-global state.
8. Gateway selected-model protocol errors do not retry or activate fallback.
9. Missing v1 response echo fails before a streamed delta is relayed or a tool call executes.
10. Chat Completions, Anthropic, Responses, Gemini, and Bedrock mappings match their native contracts.
11. Gateway Ollama `/api/chat` extension mapping is covered; `/api/generate` mandatory selection fails before dispatch.
12. Unsupported transport/model combinations fail rather than downgrade.
13. Deferred `tool_call` remains scoped and schema-validated by Hermes.
14. Stable system prompt, tool ordering, tool schemas, and prompt-cache key are unchanged.
15. Strict role alternation and native tool-result pairing remain valid.
16. Cancellation, abandoned streams, new conversations, and resets clear one-shot state.
17. Concurrent title generation cannot observe an active primary-turn policy.
18. Call-role headers are recomputed, allowlisted, and behaviorally inert.
19. Sanitized fixtures contain no credentials, private connector identifiers, or connector output.
20. Existing optional and tool-less conversation-loop tests remain unchanged and green.

## 17. Rejected alternatives

### Natural-language intent parsing

Rejected because prompt text is untrusted and ambiguous. Phrases such as “call exactly once” cannot safely authorize a side effect.

### Agent-global mandatory state

Rejected because retries, auxiliary calls, concurrent titles, compression, and later turns could inherit stale behavior.

### Synthetic user correction messages

Rejected because they break role semantics, persistence, cache stability, and the ownership boundary. Gateway corrections stay inside one ACP prompt sequence.

### Changing the stable toolset

Rejected because progressive disclosure depends on stable bridge tools and prompt caching depends on a byte-stable prefix.

### Forwarding `X-Hermes-Session-Id` as `X-Session-Id`

Rejected pending a separate tenant-isolated, delta-session design and experiment.

### Environment feature flag

Rejected. Enablement is explicit and request-scoped through tool policy plus the v1 operation marker.

## 18. Security checklist

- [ ] Policy originates only from explicit host/API control.
- [ ] Inbound OTTO contract headers are not blindly forwarded.
- [ ] Missing Gateway v1 echo fails before tool execution.
- [ ] Protocol errors cannot trigger model/provider fallback.
- [ ] Mandatory policy is immutable per attempt and cleared after a structured call.
- [ ] Auxiliary calls always construct automatic policy.
- [ ] Tool permissions remain in the Hermes registry/dispatcher.
- [ ] Tool output remains untrusted data.
- [ ] Stable prompt and tool prefix remain byte-identical.
- [ ] No synthetic conversation message is introduced.
- [ ] No private connector data appears in tests, logs, or design examples.

## 19. Coordinated ownership

Hermes owns:

- explicit user/workflow intent;
- immutable policy lifecycle;
- provider-native mapping;
- v1 emission and echo verification;
- tool execution and deferred-name authorization; and
- terminal error/fallback behavior.

OTTO Gateway owns:

- canonical surface normalization;
- ACP policy and tool-result representation;
- hidden-wrapper classification without execution;
- bounded same-model corrections;
- native error rendering; and
- Gateway/Kiro diagnostics.

Stateful session propagation remains outside both implementations for this change.
