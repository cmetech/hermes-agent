# LLM prompt — adversarial review of the explicit per-turn tool contract

> Paste everything below the line into a **fresh** coding session using a capable model or
> agent that did **not** implement this feature. Give it read access to the Hermes checkout at
> `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`. This is a hostile,
> evidence-driven review of the Hermes Tasks 1–9 implementation. The reviewer may run tests and
> create disposable test probes, but must not modify production files, commit, push, merge, tag,
> publish, release, deploy, or touch the OTTO Gateway repository.

---

## Role

You are a hostile senior Python, LLM-transport, protocol, concurrency, and application-security
reviewer. Your job is to **break and disprove** the Hermes explicit per-turn tool-calling contract,
not to bless it. Assume passing tests may assert the wrong property, mocks may hide production
behavior, comments may repeat an unproven claim, and locally correct components may fail when
composed across API negotiation, provider adapters, raw-response handling, streaming, retries,
fallback, cached agents, auxiliary calls, front-end one-shot controls, and tool execution.

Review this exact checkout and implementation range:

```text
Repository:         /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
Branch:             feat/explicit-per-turn-tool-contract-v1
Base:               0d6e8514a6f281dc414ad714277ba812a81e4d28
Implementation tip: 68b9527772462747d6698b2a2716488739a242c8
Review diff:         0d6e8514a..68b952777
```

In this fork, `base` is the development main. Literal `main` is synchronization-only and is not
the comparison branch. Do not switch to literal `main`, use it as the merge base, or mutate Git
state relative to it.

The checkout HEAD may be `68b952777` or a descendant whose only additional change is this review
prompt or another review artifact. First verify the branch, confirm `68b952777` is an ancestor,
inspect `68b952777..HEAD`, and stop with a scope error if that later range contains any production
change or unrelated file. Review **every changed file** in `0d6e8514a..68b952777`. Planning and
design files define promised behavior and release gates, but they are not evidence that the
implementation is correct.

Ground every finding in actual source, tests, and command output. Do not accept this prompt, the
design, plan, commit messages, comments, test names, or the implementation report as proof. A
passing test proves only what its assertions observe. Every finding must include a reachable
failure scenario: an exact request, policy object, header set, provider response, retry sequence,
streaming interleaving, cancellation point, concurrent operation schedule, message history, or
tool-call/result sequence leading to a wrong observable result.

This is a read-and-verify review. You may create a disposable test probe when static reasoning is
insufficient, but remove it before finishing and prove the worktree returned to its starting state.
Do not edit production code or documentation. Use sanitized tool names and fixtures only. Never
place prompts, model text, tool schemas, arguments, tool results, credentials, raw headers, raw
session identifiers, or private project identifiers from a real interaction in logs or reports.
Sanitized synthetic requests and provider-response fixtures are allowed when required for a
reproducible finding. Preserve every pre-existing tracked and untracked file. Never use broad
staging, destructive Git commands, or recursive cleanup.

## Authoritative contract — read completely before reviewing

Read every applicable `AGENTS.md` first, then read these Hermes files completely and in order:

1. `docs/design/2026-08-15-explicit-per-turn-tool-choice-contract.md`
2. `docs/plans/2026-08-15-explicit-per-turn-tool-choice-contract.md`
3. `docs/2026-08-13-required-tool-choice-planning-prompt.md`
4. `docs/design/2026-08-12-deferred-tool-dispatch-findings.md`
5. `docs/plans/2026-08-12-deferred-tool-dispatch-reliability-plan.md`

Also read the approved Gateway boundary documents without modifying that repository:

1. `/Users/coreyellis/code/github.com/cmetech/otto_app/otto-gateway/docs/superpowers/specs/2026-08-15-model-selection-aware-tool-contract-design.md`
2. `/Users/coreyellis/code/github.com/cmetech/otto_app/otto-gateway/docs/superpowers/plans/2026-08-15-model-selection-aware-tool-contract.md`

If those Gateway paths are absent from the primary checkout, locate the approved copies in its
existing `model-selection-aware-tool-contract` worktree and record that substitution. Do not infer
authorization to inspect unrelated Gateway changes or run live Gateway operations.

The 2026-08-15 Hermes design is the product authority for the Hermes v1 request contract. Its plan
defines the promised Tasks 1–10 sequence. The older Hermes findings and plan remain authoritative
for inherited deferred-dispatch, authorization, message-pairing, and tool-execution invariants. The
Gateway design defines the other side of the wire boundary; Hermes must not silently invent a
different header, error code, lifecycle, or payload meaning. If implementation and design disagree,
report the disagreement rather than reinterpreting the design to match code.

## Scope verification

Before looking for bugs:

1. Record the exact branch, HEAD, `base` merge base, and starting worktree state.
2. Confirm the implementation commits are exactly:

   ```text
   c632c7c1e docs: approve explicit per-turn tool contract
   63f3e70e2 feat: model per-attempt tool choice policy
   8d8422d34 feat: accept explicit per-request tool policy
   2f201f712 feat: add one-shot tool choice controls
   b9a8af57b feat: map tool policy across provider transports
   e3370f49a feat: enforce otto tool contract echo
   e9ff18bf3 feat: scope mandatory tool policy to one operation
   feeb1fb9c test: isolate tool policy from auxiliary requests
   2470cfa7a feat: surface tool contract failures without fallback
   295b0175b feat: add bounded tool contract diagnostics
   68b952777 fix: preserve tool policy request compatibility
   ```

3. Use `git diff --name-status 0d6e8514a..68b952777` as the authoritative inventory.
4. Distinguish implementation defects from pre-existing behavior by proving changed-code causality.
5. Confirm no OTTO Gateway file or repository is included in the review range.
6. Record all pre-existing untracked paths without reading, modifying, staging, or deleting them.

## Locked invariants to falsify

Produce an explicit **PASS / FAIL / UNPROVEN** matrix for every invariant. A matching test name is
not proof: trace the production path and name the assertion or probe that would fail if the
invariant regressed.

1. Tool policy comes only from explicit structured host/API control and has exactly four semantic
   modes: `auto`, `required`, `named(<authorized tool>)`, and `none`. Prompt phrases such as “call
   exactly once” never create or alter policy.
2. Policy and operation metadata are immutable, request-scoped values. No environment variable,
   process global, mutable agent field, session database row, prior message, system prompt, tool
   definition, or shared cached-agent property can activate policy for another operation.
3. The current attempt's `tool_choice` is the semantic source of truth. Initial mandatory attempts
   use required or a validated named choice; a valid structured tool call immediately derives a
   post-tool attempt with `auto` while retaining the same v1 logical-operation context.
4. Same-network-attempt retries deliberately reuse the same immutable attempt context. A new
   attempt, unrelated retry, title, compression, summary, probe, delegation, or fallback
   initialization starts with fresh automatic/no-v1 context.
5. Final answer, cancellation, terminal error, maximum-iteration exit, new/reset conversation, and
   abandoned stream clear operation references. Two concurrent operations using one cached agent
   cannot observe or overwrite each other's policy.
6. API request parsing accepts supported structured `tool_choice` forms, rejects malformed shapes
   and unknown named tools with a stable typed category, and includes normalized policy plus exact
   contract version in idempotency identity without incorporating arbitrary headers.
7. Only an inbound `X-Otto-Tool-Contract` whose trimmed value is exact `v1` creates trusted v1
   operation context. Hermes never blindly forwards an arbitrary inbound value, reflects it, or
   enables v1 for an absent, case-variant, duplicate, comma-joined, or unsupported value.
8. Hermes generates the exact downstream static v1 header from validated context. Direct providers
   receive no OTTO-specific headers. A v1 operation on a transport that cannot inspect response
   headers fails before dispatch.
9. `X-Otto-Call-Role` is allowlisted diagnostic metadata only. Missing, invalid, or changed values
   cannot authorize tools, select a model, alter policy, enable Gateway recovery, or affect tool
   execution.
10. Exact Gateway echo is verified before parsing or returning non-streaming content, before
    relaying the first streaming delta, and before executing any surfaced tool call. Missing or
    incorrect echo closes the response and becomes terminal `otto_tool_contract_unavailable`.
11. OpenAI Chat Completions maps required to `"required"`, named to the function object, and none to
    `"none"`. Responses/Codex uses per-attempt policy instead of a hard-coded `"auto"`.
12. Anthropic Messages maps required to `{"type":"any"}`, named to
    `{"type":"tool","name":"..."}`, and none to tool omission/native-none behavior. Gemini maps
    required to `ANY`, named to `ANY` plus the allowed name, and none to `NONE`. Bedrock Converse
    maps required to `toolChoice.any`, named to `toolChoice.tool`, and none to tool omission or its
    supported native equivalent.
13. Required/named with no tools, an unknown name, an incompatible mode, or an unsupported
    model/transport fails as `mandatory_tool_choice_not_supported`. Hermes never strips the policy,
    switches providers, changes the selected model, or silently routes through `auto` to make it
    work.
14. An explicitly selected model remains authoritative for the initial call, retry, and post-tool
    continuation. Gateway protocol and echo failures never activate provider/model fallback.
15. `unsupported_tool_contract_version`, `mandatory_tool_choice_not_supported`,
    `otto_tool_contract_unavailable`, `selected_model_tool_protocol_failed`, and
    `selected_model_tool_result_provenance_failed` are terminal, allowlisted, protocol-native, and
    privacy-safe in streaming and non-streaming API envelopes.
16. Hermes remains the external tool host and executor. Headers and policy do not grant connector
    authorization. Both the surfaced outer tool and any deferred inner tool are validated against
    the effective scoped catalog and schema before execution.
17. Native tool-call/tool-result pairing and strict role alternation remain valid. No synthetic
    user message is inserted, no prior message is rewritten to carry policy, and structured calls
    transition before execution without separating a call from its result.
18. The system prompt and stable ordered tool prefix remain byte-identical across initial and
    post-tool attempts. Contract version, call role, operation identity, policy, and headers do not
    participate in the stable prompt-cache key.
19. One-shot CLI, gateway, and TUI controls are front-end/session-owned, visible to the user,
    consumed by exactly one accepted operation, and cleared on success, cancellation, error,
    new/reset, or abandoned submission without transcript text.
20. Hermes does not equate `X-Hermes-Session-Id` with Gateway `X-Session-Id`, propagate either
    identifier across that boundary, or treat a session identifier as provenance or authorization.
21. Diagnostics contain only bounded operation correlation, allowlisted role, explicit/auto model
    status, policy, contract version, transport, echo/structured-call/post-tool booleans,
    allowlisted terminal code, and retry/fallback decision. They contain no prompts, model text,
    schemas, arguments, tool results, credentials, raw headers, raw session IDs, or private project
    identifiers.
22. Contract absence and ordinary `auto` preserve legacy behavior. Provider kwargs, reasoning
    settings, stable tools, retry/compression/fallback semantics, streaming callbacks, persistence,
    and user-visible output do not change except where the explicit v1 operation requires it.

## Change map — verify, do not trust

| Area | Landmarks | Intended responsibility |
|---|---|---|
| Policy model | `agent/tool_choice_policy.py` | Parse structured policy, freeze attempt/operation context, derive post-tool auto |
| Front-end one-shot state | `agent/tool_choice_control.py`, CLI/gateway/TUI command modules | Hold policy outside the agent and consume it once without transcript mutation |
| API negotiation | API-server request handlers and run executors | Parse exact inbound v1, normalize policy, validate named tools, preserve idempotency |
| Central request construction | agent conversation and request-builder seams | Carry explicit attempt context without mutating tools, messages, or cached agent state |
| Provider transports | chat, Responses/Codex, Anthropic, Gemini, and Bedrock adapters | Translate the same semantic policy to each provider's native dialect or fail explicitly |
| OTTO wire contract | `agent/otto_tool_contract.py` and raw-response call paths | Generate trusted headers and verify exact echo before content, deltas, or tool calls |
| Lifecycle | conversation loop | Reuse context for one network-attempt retry, derive post-tool auto, clear on terminal paths |
| Error classification | error classifier and API renderers | Extract only allowlisted structured codes; suppress retry/fallback and render native errors |
| Isolation | auxiliary client, title, compression, probes, delegation, cache logic | Force fresh automatic/no-v1 context and preserve stable prompt/tool identity |
| Diagnostics | `agent/tool_contract_telemetry.py` and call sites | Emit bounded content-free contract outcomes without sensitive/high-cardinality values |
| Regression coverage | contract, transport, lifecycle, API, CLI/gateway/TUI tests | Prove semantics at real seams rather than only testing frozen value objects |

Late commits may deliberately touch files outside abbreviated plan lists. Judge whether those
changes are necessary production wiring or unreviewed scope expansion; do not ignore them.

## Attack campaign 1 — request parsing, policy source, and one-shot controls

Drive every supported Hermes API surface with omitted, `auto`, `required`, `none`, valid named,
unknown named, malformed object, array, number, null, duplicate, and conflicting policy forms.
Drive contract headers with absent, empty, exact `v1`, whitespace-padded, case-variant,
comma-joined, duplicate, oversized, control-character, and unknown values. Verify whitespace is
trimmed before the exact allowlist comparison, as required by the approved Gateway contract.

- Prove user prose cannot activate policy even when it contains exact command-like phrases.
- Prove unsupported header versions fail before agent creation, provider dispatch, session
  mutation, streaming preparation, or idempotency persistence.
- Prove one v1 request cannot affect the following headerless request through adapter, cached agent,
  session, command state, context variable, class attribute, or mutable default.
- Verify normalized policy and exact contract version participate in idempotency identity while raw
  headers and call-role values do not.
- Attack one-shot controls with status-only queries, repeated submission, concurrent submission,
  invalid named selection, cancellation before agent readiness, build failure, new/reset, and
  abandoned stream. Exactly one accepted operation may consume pending state.
- Confirm command aliases and all CLI, gateway, and TUI routes reach the same semantics without
  injecting a synthetic transcript message.

Any prose-derived policy, cross-request leak, or unsupported-version dispatch is at least **High**.

## Attack campaign 2 — immutable lifecycle, concurrency, and cleanup

Trace the exact policy object from request entry through API kwargs, network call, normalized
structured call, tool execution, post-tool call, and terminal cleanup.

- Mutate caller-owned dictionaries and lists after parsing; prove frozen policy/context values do
  not change.
- Force a retry before response headers, after headers but before parsing, and at provider transient
  failure boundaries. Verify retries of one network attempt reuse the same context, while a newly
  constructed attempt does not inherit it accidentally.
- Run required and differently named operations simultaneously through the same cached agent. Add
  barriers around request construction and response normalization to maximize interleaving.
- Cancel before agent creation, during header wait, after echo, during stream iteration, during tool
  execution, and before post-tool dispatch. Check every local reference and front-end pending state.
- Force maximum iterations, empty response, provider exception, interrupt, tool exception,
  serialization exception, and terminal contract error. No policy may remain on the agent or be
  visible to the next request.
- Search for mutable defaults, agent attributes, globals, `ContextVar` misuse, session persistence,
  closure capture, and callbacks retaining operation context after completion.

A shared-agent policy leak or wrong-policy concurrent dispatch is **Critical**. Missing cleanup that
can affect a later operation is **High**.

## Attack campaign 3 — provider mappings and fail-closed capability checks

Independently inspect the final native request object for every policy on OpenAI Chat Completions,
Responses/Codex, Anthropic Messages, Gemini, and Bedrock Converse.

- Test tool lists with zero tools, one allowed tool, multiple tools, duplicate names, malformed
  schemas, renamed tools, and a named choice missing after effective-scope filtering.
- Verify named mappings select only the authorized exact name and preserve stable tool definitions
  and ordering.
- Exercise `none` with each transport's supported native behavior and prove omitted tools cannot
  reappear through a default builder branch.
- Exercise unsupported model/provider combinations, headerless transports, direct providers, and
  capability probes. They must fail before dispatch rather than silently remove policy or route to
  another provider/model.
- Confirm reasoning settings, service tier, max tokens, temperature compatibility, and provider-
  specific kwargs remain unchanged when policy is added.
- Search every provider entry point for old hard-coded `auto`, builder bypasses, alternate client
  construction, and fallback request paths that omit attempt context.

Silent downgrade of required/named or explicit model/provider switching is **Critical**. Incorrect
native mapping without a model switch is **High**.

## Attack campaign 4 — trusted OTTO headers and exact echo enforcement

Treat both caller-controlled request headers and Gateway response headers as hostile input.

- Prove outbound v1 and call-role headers are generated from validated context, not copied from the
  incoming request or arbitrary caller dictionaries.
- Attempt CR/LF, mixed case, whitespace, duplicate headers, comma folding, multiple response header
  values, proxy normalization, absent headers, wrong version, and header values on typed errors.
- Compare primary and post-tool attempts. Both retain exact v1 for one logical operation; unrelated
  auxiliary/direct-provider calls receive no OTTO headers.
- Exercise SDK raw-response APIs, context managers, exceptions before raw response creation,
  responses with inaccessible headers, and transports that return already-parsed objects.
- Verify a transport unable to expose headers fails before sending an OTTO v1 request.
- Prove missing/wrong echo closes or abandons the response safely and emits exactly one terminal
  `otto_tool_contract_unavailable`, with no retry, fallback, parse, callback, or tool execution.

Blind header forwarding or content/tool exposure before echo validation is **Critical**. A missing
fail-closed path is **High**.

## Attack campaign 5 — streaming commitment and execution ordering

Model byte- and event-level interleavings for Chat Completions, Responses/Codex, and any Anthropic
OTTO route. Do not accept final collected output as proof of first-byte safety.

- Place missing or wrong echo on responses whose first chunk is prose, reasoning, a native tool
  call, an incrementally assembled tool name, or partial arguments.
- Verify echo before the first user callback, queue insertion, SSE/response frame, spinner/tool
  progress event, persistence mutation, normalized message append, or executor invocation.
- Split a tool call at every chunk boundary, including name and JSON-argument boundaries. No partial
  call may be surfaced or executed before echo validation and complete schema validation.
- Race cancellation/client disconnect against raw-response entry, header validation, iterator
  acquisition, first chunk, final chunk, tool callback, and response cleanup.
- Exercise non-streaming content, non-streaming native calls, streaming empty results, provider
  terminal errors, malformed chunks, and errors raised while closing the raw response.
- Confirm one terminal native error is returned before assistant deltas and that suppressed model
  text or tool arguments never enter logs or error envelopes.

Any pre-echo delta, content return, or tool execution is **Critical**.

## Attack campaign 6 — structured-call transition and conversation integrity

Attack the boundary between response normalization, policy derivation, message append, and Hermes
tool execution.

- Initial response: valid native call, malformed call, unknown tool, unauthorized tool, schema-
  invalid arguments, direct wrapper text, narrated wrapper text, prose only, empty response, and
  multiple calls.
- Confirm only a valid normalized structured call derives post-tool `auto`; prose, malformed calls,
  and rejected tools must not advance lifecycle as if execution succeeded.
- Prove the transition happens before tool execution without changing the authorization decision or
  causing a failed tool to be treated as trusted provenance.
- Inspect messages after sequential and concurrent multi-tool execution. Maintain native assistant
  tool-call/tool-result pairing and strict role alternation without a synthetic user turn.
- Confirm post-tool calls retain the exact operation/version but use `auto`, even after retries,
  empty/error tool results, multiple tools, or incremental persistence.
- Force final prose, another tool call, cancellation, provider error, maximum iterations, and
  compression after tool execution. Context must remain correct for the current attempt and clear
  at the terminal boundary.

Wrong-policy post-tool calls, broken call/result pairing, or synthetic-user insertion are **High**;
authorization expansion is **Critical**.

## Attack campaign 7 — explicit-model authority, retry, and fallback

Trace requested model/provider identity through agent creation, request kwargs, SDK selection,
network retry, provider fallback logic, error classification, and post-tool continuation.

- Use explicit valid-looking, explicit unknown, empty, and auto selections; direct and OTTO routes;
  credential rotation; transient errors; rate limits; timeouts; and initialization failures.
- For mandatory policy, force unsupported capability, missing echo, wrong echo, each Gateway typed
  error, malformed error bodies, wrapped SDK errors, and generic 500/502 responses.
- Prove allowlisted contract errors are terminal before generic retry and fallback classifiers.
- Try to hide a terminal code in exception text without a structured error body and to inject an
  unrecognized code through a structured body. Hermes must neither trust arbitrary strings nor
  downgrade a recognized code.
- Confirm the selected model never becomes `auto`, another provider, or a fallback model to satisfy
  required/named policy. If an approved non-protocol provider fallback exists, prove it preserves
  semantics and capability or fails explicitly.
- Verify fallback initialization, credential probes, and routing probes use fresh automatic/no-v1
  context rather than the failed operation context.

Explicit-model switching, protocol-error fallback, or retry after a missing/wrong echo is
**Critical**.

## Attack campaign 8 — authorization and deferred-dispatch boundaries

Treat every surfaced tool name and argument as untrusted model output, regardless of policy or
Gateway participation.

- Required policy means “some authorized tool,” not permission for any name. Named policy means the
  exact effective scoped tool only.
- Exercise toolsets disabled after request parsing, service-gated tools, duplicate/aliased names,
  plugins, per-platform catalogs, malformed schemas, and catalog changes between parse and execute.
- Attack the stable outer dispatcher with missing, unknown, disabled, and schema-invalid deferred
  inner tools. Validate both outer and inner layers against the effective scoped catalog at the
  execution boundary.
- Try wrapper-like model prose, nested wrapper JSON in arguments, a named inner tool when only the
  outer dispatcher is exposed, and multiple inner candidates.
- Confirm headers, exact v1, call role, selected model, Gateway correction, and apparent provenance
  never bypass registry checks, approval policy, connector/service gates, or schema validation.
- Compare sequential and concurrent tool execution paths and all sibling dispatch helpers.

Execution or direct surfacing of an unauthorized outer or inner tool is **Critical**.

## Attack campaign 9 — auxiliary isolation, stable prefixes, and caching

Capture the exact messages, tools, request context, and cache key for initial, retry, post-tool,
title, compression, summary, probe, delegated, fallback-initialization, and unrelated operations.

- Compare system prompts and serialized ordered tool definitions byte-for-byte between base and
  feature for headerless auto, required initial, and post-tool auto calls.
- Prove policy, contract header, role, and operation identity are absent from the stable cache-key
  material while the actual per-attempt native `tool_choice` remains correct.
- Trigger title generation and compression during or immediately after a mandatory operation. Use
  barriers to interleave auxiliary request construction with the primary operation.
- Exercise summaries at maximum iterations, model capability probes, credential/account probes,
  delegated agents, middleware retries, background review, and fallback client initialization.
- Search for helper defaults or closures that implicitly capture a caller's attempt context.
- Confirm compression and replay do not persist policy into rewritten history, SessionDB, summary
  text, prefill messages, or restored sessions.

A cache-key/prefix mutation that defeats prompt caching or an auxiliary request inheriting
mandatory v1 policy is **High**.

## Attack campaign 10 — errors, diagnostics, privacy, and test integrity

Trace every new error and diagnostic from production call site through logs, API envelopes,
monitoring emission, and tests.

- Enumerate every emitted field and attempt to inject prompts, model text, schemas, arguments, tool
  results, credentials, endpoints with secrets, raw headers, connector identifiers, operation IDs,
  and raw session IDs.
- Verify operation correlation is bounded/hashed and that policy, role, transport, model status,
  terminal codes, and outcomes are closed enums with safe fallbacks.
- Confirm logging a protocol error suppresses generic request dumps and provider exception paths
  that normally include model, endpoint, response body, or arguments.
- Independently parse Chat Completions and Responses streaming/non-streaming error envelopes. Check
  status, code, message, event order, and absence of internal Gateway/provider details.
- Identify tests that exercise real conversation/request/transport composition versus tests that
  stop at a fake builder, fake raw response, fake agent, or direct pure-function call.
- Inspect concurrency tests for deterministic barriers, lifecycle tests for meaningful terminal
  assertions, streaming tests for actual callback/commit ordering, and fallback tests for production
  classifier wiring.
- Search for skips, permissive assertions, stale test doubles, duplicated table cases, environment-
  gated live tests, broad selectors that collect nothing, and tests that merely assert comments or
  mock call counts.
- Name the single highest-risk behavior not truly proved by Tasks 1–9.

Sensitive-content logging or a terminal protocol error that exposes suppressed model/tool content
is **Critical**.

## Mandatory verification

Run from the Hermes implementation checkout. Record exact commands, exit codes, skips, warnings,
retries, flakes, and unavailable tools. Prefer `.venv`; use `venv` only if that is the checkout's
configured environment. Do not silently substitute narrower commands.

```bash
git status --short --branch
git branch --show-current
git merge-base base HEAD
git rev-parse HEAD
git log --oneline --reverse 0d6e8514a..68b952777
git diff --check 0d6e8514a..68b952777
git diff --name-status 0d6e8514a..68b952777

.venv/bin/python -m pytest -q \
  tests/agent/test_tool_choice_policy.py \
  tests/agent/test_tool_choice_lifecycle.py \
  tests/agent/test_otto_tool_contract.py \
  tests/gateway/test_api_tool_choice_contract.py

.venv/bin/python -m pytest -q tests/agent/test_transports.py

.venv/bin/python -m pytest -q tests/agent \
  -k 'otto_tool_contract or raw_response or echo or selected_model or no_fallback'

.venv/bin/python -m pytest -q tests/gateway/test_api_server.py \
  tests/gateway/test_api_server_runs.py \
  tests/test_lazy_session_regressions.py \
  tests/test_tui_gateway_server.py \
  -k 'tool_choice or protocol or stream or error or prompt_submit'

.venv/bin/python -m pytest -q tests/agent/test_tool_contract_telemetry.py \
  tests/cli/test_tool_choice_control.py \
  tests/gateway/test_tool_choice_control.py \
  tests/tui_gateway/test_tool_choice_control.py

.venv/bin/python -m compileall -q agent gateway hermes_cli tui_gateway
git diff --check
```

Run the repository's canonical full suite. It intentionally isolates test files to avoid duplicate
module basenames and shared module/logging state; do not replace it with import-mode changes or add
`__init__.py` files to test trees:

```bash
scripts/run_tests.sh -q
```

If the canonical runner reports a file as flaky after a successful built-in retry, record the
first-attempt failure and retry result separately. Do not conceal the flake or misreport it as a
final suite failure.

Run privacy and stable-surface checks over the implementation range. An `rg` exit code of 1 means
no match and must be recorded as such:

```bash
git diff --quiet 0d6e8514a..68b952777 -- \
  agent/prompt_builder.py toolsets.py model_tools.py tools/registry.py

git diff 0d6e8514a..68b952777 -- . ':(exclude)docs/**' | \
  rg -n 'Authorization:|Bearer |X-Session-Id|X-Hermes-Session-Id'
```

Do not run live Gateway, provider, wrapper, release, deployment, or credential-bearing commands.
Do not contact real connectors or use the operator's real configuration. A deterministic disposable
probe may use only isolated test-owned paths, mocked Gateway responses, and sanitized data.

## Known baseline exclusions and accepted scope

Do not attribute these to this branch without proving changed-code causality:

1. The checkout contains numerous pre-existing untracked `.otto/`, planning, assessment, review,
   and handoff files. Do not read, modify, stage, delete, or report their contents as findings.
2. Literal `main` is synchronization-only. The feature correctly starts from `base`; do not call
   that branch convention a defect.
3. The full suite's canonical runner uses per-file subprocess isolation because raw single-process
   collection has duplicate test-module basenames. Do not add missing `__init__.py` files: their
   absence can be load-bearing for plugin import isolation.
4. Gateway Task 10, deployed direct v1 probes, real model behavior, and live Hermes-to-Gateway
   integration are intentionally absent. Mark them **UNPROVEN** release evidence rather than code
   defects or fabricated passes.
5. The feature does not promise natural-language policy inference, a process-wide feature flag,
   provider switching to satisfy mandatory selection, connector authorization through headers, or
   session-based provenance.
6. `auto` permits ordinary model behavior. `none` deliberately disables tools. `required` requires
   an authorized tool but does not select which one. `named` selects only one authorized exact tool.
7. Direct providers do not receive OTTO headers. This is intentional, not missing propagation.
8. Task 10's future verification artifact and coordinated live tests are separate and must wait for
   the Gateway release gate. Do not create them during this review.
9. No deployment, release, push, merge, tag, or cross-repository mutation is authorized.

If a tool or platform is unavailable, record it as unavailable. Do not replace it with a narrower
command and claim equivalence.

## Severity

- **Critical** — an unauthorized outer/inner tool executes; a pre-echo delta/content/tool call
  escapes; one operation's policy affects another; an explicit model/provider silently changes;
  Gateway protocol/echo failure retries or falls back; prompts, model text, schemas, arguments,
  tool results, credentials, raw headers, or raw session IDs leak through ordinary errors/logs/
  diagnostics; exploitable race, deadlock, or unbounded retry.
- **High** — unsupported contract dispatches; required/named silently downgrades; provider native
  mapping is wrong; post-tool remains mandatory instead of auto; lifecycle cleanup fails; synthetic
  messages or broken tool-call/result pairing appear; auxiliary calls inherit policy; stable prompt/
  tool prefix or cache identity changes; one-shot state is consumed by the wrong operation;
  streaming/native error timing is incorrect without content exposure.
- **Medium** — bounded telemetry is materially incorrect; safe error code/status drift; call-role or
  transport misclassification without authority expansion; meaningful performance/allocation
  regression; architecture or documentation drift likely to cause unsafe integration.
- **Low** — weak or misleading test, stale test double, minor naming/wording ambiguity,
  maintainability defect, or unavailable evidence described incorrectly without a current runtime
  fault.

Do not inflate severity. A theoretical concern without a reachable input and wrong observable
outcome is not a finding. Conversely, do not downgrade cross-request policy leakage, pre-echo tool
execution, authorization expansion, selected-model fallback, or sensitive-content exposure because
the triggering provider output or race is unusual.

## Deliverable

Produce one self-contained adversarial review with this exact structure:

1. **Scope verification** — branch, HEAD, ancestry, exact review range, review-only descendant,
   starting worktree state, changed-file inventory, and unavailable tools.
2. **Verdict** — `SHIP`, `SHIP WITH FOLLOW-UPS`, or `DON'T SHIP`, with the single most important
   reason. This verdict covers Tasks 1–9 code readiness only, not live coordinated release.
3. **Findings table**, sorted by severity:
   `file:line | severity | invariant violated | defect | concrete failure scenario | minimal fix`.
4. **Top-five reproductions** — exact sanitized request/headers, provider response, message/tool
   sequence, streaming interleaving, concurrency schedule, cancellation sequence, or runnable
   disposable test and the wrong observable result.
5. **Invariant matrix** — all 22 locked invariants marked PASS / FAIL / UNPROVEN with production
   source and test/command evidence. No omitted rows.
6. **Test-integrity assessment** — distinguish real composition coverage from pure-unit and mocked
   transport coverage; name the highest-risk behavior the suite does not truly prove.
7. **What I verified safe and why** — concrete reasoning for areas with no finding, especially
   request scope, concurrency, provider mappings, exact echo timing, post-tool auto transition,
   selected-model authority, authorization, auxiliary isolation, and cache stability.
8. **Verification evidence** — exact commands, exit codes, skips, warnings, retry/flake output,
   compile result, diff checks, privacy scans, and final worktree status.
9. **Required remediation before ship** — ordered minimal fixes for every Critical/High finding and
   the focused regression test each fix needs.
10. **Release-evidence gaps** — deployed Gateway commit/version, direct exact-v1 probes, Gateway
    release gate, real provider/model behavior, and Hermes Task 10 integration explicitly marked
    UNPROVEN and separated from code findings.

Do not stop at the first bug. Do not spend the report praising architecture. Do not report a
concern without tracing it to a reachable wrong outcome. Be specific or be silent.
