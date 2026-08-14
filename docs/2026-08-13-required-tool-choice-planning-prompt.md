# Planning prompt — require tools only on mandatory tool-decision turns

> Copy everything below the horizontal rule into a fresh session whose working
> directory is
> `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`.
> This is a design-and-planning task. Discuss the design with the user and get
> approval before writing an implementation plan or changing production code.

---

Work in:

```text
/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
```

We need to design and plan how Hermes signals that a model must make a tool
call on a mandatory tool-decision turn. Do not implement the change yet.

## Before proposing a design

1. Read every applicable `AGENTS.md` completely, including the workspace-level
   file in the parent directory.
2. Report the current branch and working-tree state. Preserve all unrelated
   changes; the checkout may already contain untracked planning and review
   documents.
3. Trace the real request-building flow before deciding where the behavior
   belongs. At minimum, inspect:
   - `agent/chat_completion_helpers.py`
   - `agent/transports/chat_completions.py`
   - `agent/transports/anthropic.py`
   - `agent/anthropic_adapter.py`
   - `agent/transports/codex.py`
   - the OTTO/LOOP24 provider-profile and model-selection paths
   - the conversation/tool loop that builds the first tool-decision request
     and the later post-tool final-answer request
   - existing tests covering request kwargs, tool calls, prompt caching, and
     provider transport parity
4. Read the Gateway contract for context:
   - `/Users/coreyellis/code/github.com/cmetech/otto_app/otto-gateway/docs/superpowers/specs/2026-08-13-explicit-model-tool-protocol-recovery-design.md`
   - `/Users/coreyellis/code/github.com/cmetech/otto_app/otto-gateway/internal/engine/tool_protocol.go`
5. Review relevant history with `git log -p -S 'tool_choice'` and inspect recent
   request-builder changes. Verify intent instead of assuming an omission.

## Observed problem

The same mandatory GitLab connector request was tested through the OTTO
Gateway with `auto`, Sonnet, an explicitly selected GPT model, and Qwen3.
`auto` and Sonnet produced valid deferred-dispatcher tool calls. The other two
selected models returned ordinary text instead of a structured call:

- one emitted an anchored `ToolUnavailable: ... is not available in this
  environment` capability refusal;
- one emitted only a title-like response and no tool call.

The Gateway now recognizes the anchored `ToolUnavailable:` failure and can
issue its one permitted corrective prompt. However, when Hermes omits
`tool_choice` or sends an optional/automatic choice, a title-only response is
protocol-valid ordinary text. The Gateway must not guess from natural-language
intent that every tool-less answer is wrong.

Hermes owns the missing semantic signal: when its current turn genuinely
requires external tool execution, its request should say so using the
provider's protocol-native required or named tool-choice form.

## Design goal

Create an evidence-backed design and implementation plan for propagating a
mandatory-tool-decision signal through Hermes request construction so the OTTO
Gateway receives an unambiguous `tool_choice` on the correct turn.

For the OpenAI-compatible deferred-dispatcher request, evaluate both:

```json
{"tool_choice":"required"}
```

and selection of the declared outer dispatcher:

```json
{"tool_choice":{"type":"function","function":{"name":"tool_call"}}}
```

The caller declares only the safe outer `tool_call` dispatcher. The inner MCP
tool name and arguments remain deferred payload handled by the existing
dispatcher contract. Do not assume the named form is preferable; compare it
with `required` against current transports and tests.

## Required properties

- Do not set `tool_choice: required` merely because a request contains tools.
  Optional-tool conversations must still allow a normal answer.
- Require a tool only when Hermes has an explicit, trustworthy indication that
  the current turn is a mandatory tool-decision turn. Do not add a broad
  natural-language intent parser.
- Do not require another tool after a tool result. The post-tool continuation
  must allow the model to produce its normal final answer.
- Preserve strict message-role alternation and the byte-stable per-conversation
  prompt/tool prefix. Prompt caching is sacred.
- Do not mutate prior messages or swap the toolset mid-conversation.
- Preserve existing behavior for ordinary chat, optional tools, no tools,
  explicit tool suppression, parallel tool calls, tool-result continuation,
  fallbacks, auxiliary calls, compression, and retries.
- Determine whether this should be OTTO-provider-specific, a general
  transport-neutral request policy, or an explicit call-site option. Prefer
  the narrowest boundary that represents the semantics correctly without
  creating brand-specific drift.
- Map the semantic choice correctly across every affected transport. Confirm
  the exact wire form rather than assuming OpenAI, Anthropic, Responses, and
  other adapters accept the same value.
- Keep the existing Gateway rule intact: an explicitly selected model remains
  authoritative and is never silently retried through model `auto`.
- Do not add dependencies, release changes, tags, pushes, or unrelated
  refactors.

## Questions the design must answer

1. Where does Hermes first know that tool execution is mandatory, and how can
   that fact be represented without inspecting arbitrary prompt prose?
2. Is the mandatory state per user turn, per API attempt, or part of a narrower
   workflow/dispatcher call contract?
3. How is the state cleared immediately after a structured tool call so the
   tool-result continuation permits a final answer?
4. Should deferred dispatch select the named outer `tool_call` function or use
   the more general `required` choice?
5. Which primary, fallback, auxiliary, compression, retry, gateway, CLI, TUI,
   and desktop paths construct requests independently and therefore need
   coverage or an explicit exclusion?
6. How will tests inspect the actual outbound request body and prove the
   post-tool request does not remain required?
7. How will this interact with prompt caching and long-lived conversations?

## Required process and deliverables

1. Begin with a concise evidence report: current branch/tree, request-builder
   call graph, current `tool_choice` behavior by transport, and any conflict
   between this request and existing architecture.
2. Ask the user clarifying questions one at a time where product semantics are
   not discoverable from code.
3. Present two or three viable designs with concrete trade-offs and a
   recommendation. At least compare:
   - an explicit per-turn/request option carried through the transport-neutral
     request builder;
   - derivation from canonical conversation state at the request boundary;
   - a narrowly provider- or dispatcher-scoped policy, if evidence supports
     it.
4. Discuss and obtain approval for the design before writing the plan.
5. Write the approved design under `docs/superpowers/specs/` and perform a
   contradiction, ambiguity, and scope review.
6. Only after design approval, create a test-first implementation plan under
   `docs/superpowers/plans/`. The plan must name exact files, tests, RED/GREEN
   commands, focused and full verification, branch/ledger implications, and
   atomic commit boundaries.
7. Stop after the plan and ask for approval to execute it. Do not implement,
   release, merge, or push in this planning session.

## Acceptance scenarios the plan must cover

| Scenario | Expected outbound policy and behavior |
|---|---|
| mandatory initial deferred-dispatch turn | required or named outer dispatcher; missing call is a protocol failure |
| valid dispatcher call | existing structured tool execution path |
| tool result followed by final answer | optional/automatic choice; final prose allowed |
| optional-tool user turn | optional/automatic choice; final prose allowed |
| tool-less user turn | no tool choice emitted |
| explicit tool suppression | no tool invocation required |
| selected model emits `ToolUnavailable:` | Gateway may perform one same-model, same-session correction |
| selected model still fails correction | Gateway's safe `selected_model_tool_protocol_failed` HTTP 502 |
| model is `auto` | existing model-routing behavior unchanged |
| fallback/auxiliary/compression request | no accidental mandatory-tool state leakage |

Use sanitized examples only. Do not include credentials, private connector
schemas, tool arguments, project names, raw model output beyond the generic
failure forms above, or upstream internal error details.
