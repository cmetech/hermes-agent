# Adversarial review — explicit per-turn tool contract (Hermes Tasks 1–9)

**Reviewer:** Opus 5 (1M), hostile read-and-verify pass
**Date:** 2026-08-15
**Prompt:** `docs/reviews/2026-08-15-explicit-per-turn-tool-contract-adversarial-review-prompt.md`

> **Sections 1–10 are the original review of `0d6e8514a..68b952777`. Section 0 below is the
> re-review of the remediation range `68b952777..d6c36c543` and supersedes the original verdict.**

---

## 0. Re-review of the remediation (range `68b952777..d6c36c543`)

### 0.1 Scope

| Item | Value |
|---|---|
| HEAD | `d6c36c54345b3741e24b1f53e23b35caa789eca7` |
| Prior tip `68b952777` is an ancestor | Yes |
| Remediation range | `68b952777..d6c36c543` — 9 commits, 19 files, `+475 / -61` |
| `git diff --check` on the range | exit 0 |
| Production files touched | `agent/{anthropic_adapter,chat_completion_helpers,conversation_loop,otto_tool_contract,tool_choice_policy}.py`, `agent/transports/{anthropic,base}.py`, `gateway/platforms/api_server.py`, `tui_gateway/server.py` |
| Gateway repo files in range | None |
| Tracked worktree modifications | None (`git diff --stat` empty) |

### 0.2 Verdict

### **SHIP WITH FOLLOW-UPS** — upgraded from DON'T SHIP

Every Critical and High finding is fixed, and each fix is load-bearing on the production path rather than only on a test double. The four remaining items are Low, none of them blocks release, and one of them is a documentation correction to the review prompt rather than to the code.

### 0.3 Disposition of the original findings

| # | Sev | Fix | Status | Evidence |
|---|---|---|---|---|
| F1 | Critical | `59aebb4ce` | **FIXED — mutation-verified** | `_open_anthropic_stream` now computes `contract_required(final_kwargs)`, wraps both `messages.stream()` and `manager.__enter__()` in `verify_exception_echo`, and calls `verify_stream_echo` before returning the stream; on failure it `__exit__`s the manager and clears `_stream_context`. See R6 below. |
| F2 | High | `a91252cf6` | **FIXED — reproduced clean** | Two layers: `_parse_api_tool_operation` returns `(None, None)` when the body has no `tool_choice` and no contract header; `resolve_tool_choice` returns `None` for `auto` with an empty catalog. See R7. |
| F3 | High | `366027221` | **FIXED** | `build_anthropic_kwargs` raises `mandatory_tool_choice_not_supported` when `thinking` is present and `tool_choice.type` is `any`/`tool`. Correctly does **not** fire for `auto` or `none`. Caveat N2 below. |
| F4 | High | `bdebfbf34` | **FIXED** | `_reset_session_agent` pops `tool_choice_control` alongside the other one-shot overrides; the existing reset test was extended. I confirmed `_reset_session_agent` is the **only** in-place conversation reset in `tui_gateway/` (`session["history"] = []` appears exactly once, at `server.py:6139`). |
| F5 | Medium | `4973c0e94` | **FIXED — reproduced clean** | A `tool_policy_preflight` future is resolved from the executor thread via `loop.call_soon_threadsafe` and awaited before SSE headers are written. Streaming and non-streaming now return byte-identical typed 400s. `error.type` is now `invalid_request_error` instead of the leaked Python class name, `completed` is `False`, and `_redact_api_error_text` is applied. See R8. |
| F6 | Medium | `6058f2e48` | **FIXED** | A `stream_echo_verified` flag stops a post-echo mid-stream exception from being re-classified as `otto_tool_contract_unavailable`. The new F1 fix is correctly scoped the same way — its `verify_exception_echo` covers only stream creation, never iteration. |
| F7 | Medium | `1aea197b4` | **FIXED** | New `response_echo_present()` reads the actual response/exception headers. A locally raised `OttoToolContractError` (no `.response`) correctly reports `echo=False`; a Gateway typed error that *did* echo reports `True`. The success-path `echo` remains version-derived, which is factually correct there (reaching normalization implies the echo was verified). |
| F9 | Low | `708626a35` | **FIXED** | `validate_tool_choice_policy` now raises `mandatory_tool_choice_not_supported` for an unknown named tool, matching `resolve_tool_choice`. Both API surfaces render 400. |
| F10 | Low | `708626a35` | **FIXED** | `headers.getall(...)` with `len > 1` → 400 `unsupported_tool_contract_version`. Verified for `v1`+`v2` **and** `v1`+`v1`. |
| F11 | Low | `708626a35` | **FIXED** | New `use_native_none` flag emits `tool_choice: {"type":"none"}` **only** under v1, preserving the legacy tools-omitted path for every other caller — the right shape, since it matches Gateway design §6's canonical Anthropic Prohibited row without changing non-contract behavior. |
| F12 | Low | `d6c36c543` | **PARTLY FIXED** | The gateway control test is a real upgrade: it now builds a real `GatewayRunner` + `SessionStore`, derives the key through `_generate_session_key`, consumes through a real `TurnRunner`/`TurnContext`, asserts `session_key.startswith("agent:profile-fixture:")`, and calls the real `_clear_conversation_scope` — closing the exact "the two key derivations are never compared" gap I raised. The placebo TUI assertion is gone. But the five tautological `agent.__dict__` assertions were **deleted rather than replaced** — see N3. |
| F8 | Medium | rejected | **REJECTION ACCEPTED** | The author is right and I was wrong to score this against the code. Hermes design §7.2 explicitly permits "Authorized fallback for the same logical attempt → deliberately translate the same policy", and the post-tool policy *is* `auto`, so the implementation conforms. The stricter wording is in the **review prompt's** invariant 14 ("…and post-tool continuation"), which is the outlier and should be amended to match the approved design. Note the implementation is in fact *stricter* than the design allows: because v1 blocks fallback outright, the design's "use v1 only when target is OTTO" fallback row is unreachable — a safe direction. |

### 0.4 New findings from the re-review (all Low; none blocking)

| # | file:line | Sev | Defect | Concrete scenario | Minimal fix |
|---|---|---|---|---|---|
| **N1** | `gateway/platforms/api_server.py:1177` (`_await_stream_tool_policy_preflight`) | **Low** | `asyncio.wait({agent_task, preflight}, return_when=FIRST_COMPLETED)` has **no timeout**. The preflight resolves immediately after `validate_tool_choice_policy`, so a slow *turn* costs nothing — but agent **creation** happens before that point, so its whole duration is now charged to time-to-first-byte, and a wedged `_create_agent` yields an HTTP request that hangs with no headers at all (previously the SSE stream opened and the queue-timeout machinery bounded it). | Measured with a 1.2 s agent build: legacy streaming request → headers in **0.03 s**; explicit-policy streaming request → **1.21 s**. With a 1.5 s *turn* both are 0.00 s, confirming only the build window is affected. Scoped to requests that actually send `tool_choice`/v1, so legacy traffic is untouched. | Pass `timeout=<agent-build budget>` to `asyncio.wait` and fall through to the SSE path on timeout. The 400 is still returned whenever validation finishes first, which is the normal case. **Disposition (post-response):** the implementer's concern that a timeout fall-through "could reintroduce F5" was **probe-disproven** — with `_await_stream_tool_policy_preflight` bypassed entirely, both SSE surfaces still render the typed in-stream error (`code=mandatory_tool_choice_not_supported`, `type=invalid_request_error`, `completed:false`, zero content deltas / `response.failed`), because the F5 fix's in-stream rendering half is independent of the preflight. The defined lifecycle is therefore: timeout constant → fall through → existing typed in-stream rendering, plus one regression test asserting that rendering with the preflight bypassed (the probe is that test and passes today). Remains open as Low. |
| **N2** | `agent/anthropic_adapter.py:3025` | **Low** | The new thinking guard triggers on `"thinking" in kwargs` regardless of `thinking.type` or endpoint. `_supports_adaptive_thinking` defaults **unknown Claude models to adaptive** and also returns True for Kimi/Moonshot Anthropic-compat endpoints, and non-Claude Anthropic-shaped endpoints (minimax, qwen3) get the manual contract — so Anthropic's extended-thinking restriction is applied to every Anthropic-shaped route and to the adaptive contract. | `required`/`named` on a Claude 4.6+/4.7 (adaptive) model, or on a Kimi/MiniMax Anthropic-compat endpoint, now hard-fails `mandatory_tool_choice_not_supported` locally. It fails **closed**, and the repo itself records that no in-tree caller passes `tool_choice` to this path (`auxiliary_client.py:1884`), so nothing that worked before breaks — but a supported modern combination may be rejected. | Scope the guard to the manual `{"type": "enabled"}` contract (and to Claude endpoints), or confirm against the vendor documentation cited in the fix that the restriction still applies to adaptive thinking. |
| **N3** | `tests/agent/test_tool_choice_lifecycle.py` | **Low** | The five `assert "tool_operation_context" not in agent.__dict__` lines were removed. Correct — they could never fail — but nothing replaced them, so cross-operation policy leakage on a cached agent now has **no** assertion. | A future refactor that stores the context on the agent would be caught by no test. | A real replacement is cheap and the existing fixture already supports it: run a mandatory v1 turn, then a second `run_conversation` on the **same** agent with `tool_operation_context=None`, and assert the second turn's captured payload has no `tool_choice` and no `X-Otto-*` headers. I ran exactly this (R9) and it passes today. |
| **N4** | `gateway/platforms/api_server.py:1139` | **Low** | The legacy gate is `"tool_choice" not in body`, so an explicit JSON `null` — which OpenAI treats as absent — still builds an `auto` context and, with tools present, still adds `tool_choice: "auto"` to the wire. | `{"model":…,"messages":[…],"tool_choice":null}` → context `auto`, non-legacy body. Verified. | Change the gate to `body.get("tool_choice") is None`. |

**Scope note (not a finding):** three **untracked, never-committed** paths are now present that were absent at the start of the original review — `tests/fixtures/fake_kiro_task10.py` (139 lines), `tests/integration/test_otto_gateway_v1_release.py` (524 lines), and `docs/verification/`. They are outside the reviewed range and I did not review, read, or run them. They are also **excluded from the canonical suite** by `scripts/run_tests_parallel.py:346` (`_SKIP_PARTS = {"integration", "e2e", "docker"}`), so the clean full-suite result below does **not** cover them — both runs report the same 2826 files. A bounded check shows they reference only `http://127.0.0.1:` (no external host), so they appear hermetic in shape, but they spawn subprocesses and were never executed. Either commit them deliberately as the Task-10 artifact and run them with `--include-integration`, or remove them; an uncommitted test file is neither reviewed nor exercised.

### 0.5 Re-review reproductions

**R6 — the Anthropic streaming guard is load-bearing (mutation proof).** Same production entry point (`agent._interruptible_streaming_api_call`), real `AIAgent`, `provider="otto"`, `api_mode="anthropic_messages"`, fake client whose stream reports `headers={}`. The only variable is whether `agent.otto_tool_contract.verify_stream_echo` is neutralized:

```
guard ACTIVE      -> raised OttoToolContractError:otto_tool_contract_unavailable
                     deltas delivered = []        cleanup = ['stream', 'manager']
guard NEUTRALIZED -> returned normally
                     deltas delivered = ['LEAKED'] cleanup = ['stream', 'manager']
```

**R7 — legacy API bodies are restored.**

```
_parse_api_tool_operation:
  plain (no choice, no hdr)  -> ctx=None                  err=None
  explicit auto              -> ctx=auto/v=None           err=None
  required, no hdr           -> ctx=required/v=None       err=None
  no choice + v1 hdr         -> ctx=auto/v=v1             err=None
  duplicate v1+v2            -> ctx=None                  err=400
  duplicate v1+v1            -> ctx=None                  err=400
  explicit null choice       -> ctx=auto/v=None           err=None   <- N4 residue

end-to-end, tool-less agent, plain request:
  agent.tools = []      WIRE keys: ['messages', 'model']   tool_choice present: False
```

**R8 — streaming and non-streaming now agree, and legacy streaming is untouched.** Real aiohttp `TestServer`, agent catalog `{"tool_call"}`, choice naming `unavailable_tool_fixture`:

```
STREAM  chat  + bad name -> 400  application/json  code=mandatory_tool_choice_not_supported  type=invalid_request_error
NONSTR  chat  + bad name -> 400  (identical payload)
STREAM  responses + bad  -> 400  (identical payload)
LEGACY  stream           -> 200  text/event-stream  elapsed 0.00s  3 frames + [DONE]
STREAM  chat  + good name-> 200  3 frames
```

**R9 — nothing regressed, and cross-operation isolation holds.** Real `AIAgent`, `provider="otto"`, loopback Gateway, `required` + v1, with an unauthorized tool name injected on the first response, run at three echo settings:

```
ECHO=v1     requests=3  executed=['tool_fixture']
   1: tool_choice='required' role=primary   contract=v1
   2: tool_choice='required' role=primary   contract=v1   <- rejected call did not advance lifecycle
   3: tool_choice='auto'     role=post_tool contract=v1
   stable prefix identical: True | tools identical: True
ECHO=none   requests=1  executed=[]  failed=True  code=otto_tool_contract_unavailable  model text leaked: False
ECHO=wrong  requests=1  executed=[]  failed=True  code=otto_tool_contract_unavailable  model text leaked: False

second turn on the SAME cached agent with tool_operation_context=None:
   tool_choice=None   X-Otto-Tool-Contract=None   X-Otto-Call-Role=None
```

### 0.6 Re-review verification

```
git status --short --branch          -> clean; no tracked modifications
git diff --check 68b952777..d6c36c543 -> exit 0

pytest -q tests/agent/test_tool_choice_policy.py tests/agent/test_tool_choice_lifecycle.py \
          tests/agent/test_otto_tool_contract.py tests/gateway/test_api_tool_choice_contract.py
                                                    -> 58 passed
pytest -q tests/agent/test_transports.py            -> 13 passed   (whole file; the prompt's -k selector still under-collects)
pytest -q tests/agent -k 'otto_tool_contract or raw_response or echo or selected_model or no_fallback'
                                                    -> 49 passed, 3 skipped   (same pre-existing nemo_relay import skips)
pytest -q tests/gateway/test_api_server.py tests/gateway/test_api_server_runs.py \
          tests/test_lazy_session_regressions.py tests/test_tui_gateway_server.py \
          -k 'tool_choice or protocol or stream or error or prompt_submit'
                                                    -> 88 passed
pytest -q tests/agent/test_tool_contract_telemetry.py tests/cli/test_tool_choice_control.py \
          tests/gateway/test_tool_choice_control.py tests/tui_gateway/test_tool_choice_control.py
                                                    -> 11 passed   (12 before; the TUI placebo was removed)
pytest -q tests/agent/test_anthropic_adapter.py tests/run_agent/test_streaming.py
                                                    -> 144 passed

scripts/run_tests.sh -q
    === Summary: 2826 files, 33459 tests passed, 0 failed (100% complete) in 849.3s (14 workers) ===
    RUNNER_EXIT=0        0 failures, 0 flaky files

python -m compileall -q agent gateway hermes_cli tui_gateway   -> exit 0
git diff --check                                               -> exit 0
git diff --quiet 0d6e8514a..d6c36c543 -- agent/prompt_builder.py toolsets.py model_tools.py tools/registry.py
                                                               -> exit 0 (authorization surfaces still byte-unchanged)
git diff 0d6e8514a..d6c36c543 -- . ':(exclude)docs/**' | rg 'Authorization:|Bearer |X-Session-Id|X-Hermes-Session-Id'
                                                               -> exit 1 (no match), full range and fix-only range
```

**Retraction of the original suite caveat.** My first pass reported 1 hard failure (`test_performance_bounds.py`, a CPU `process_time` budget) plus 3 flaky files, and I attributed them to CPU contention from probes I ran concurrently. This run was uncontended and returned **0 failed, 0 flaky**, which confirms that attribution. The earlier failure was mine, not the branch's.

### 0.7 Revised invariant matrix deltas

| # | Was | Now | Why |
|---|---|---|---|
| 6 | FAIL (partial) | **PASS** | Streaming and non-streaming both return the stable typed category before any SSE frame (R8). |
| 7 | PASS with caveat | **PASS** | Duplicate contract headers now 400. The "padded" clause remains a prompt error, not a code one — Gateway design §5.1 mandates trimming. |
| 10 | FAIL | **PASS** | The Anthropic streaming route is guarded, mutation-verified (R6); chat-completions and codex paths re-verified (R9). |
| 12 | PASS with caveat | **PASS** | v1 `none` on Anthropic now emits the Gateway-canonical `{"type":"none"}` while legacy behavior is preserved. |
| 13 | FAIL (partial) | **PASS with caveat** | The Anthropic thinking incompatibility now fails closed pre-dispatch; the guard's breadth is N2. |
| 14 | FAIL (partial) | **PASS** | Re-scored against the approved Hermes design rather than the prompt's stricter wording — see the F8 row. Amend invariant 14. |
| 15 | PASS with caveat | **PASS** | Typed code, `invalid_request_error` type, and `completed: false` are now consistent across both surfaces. |
| 19 | FAIL | **PASS** | TUI reset clears the pending control; CLI and gateway were already correct, and the gateway path is now proven end-to-end against real key derivation. |
| 21 | PASS with caveat | **PASS** | `echo` is now observed rather than derived on the terminal path. |
| 22 | FAIL | **PASS** | Legacy API bodies are byte-identical to `0d6e8514a` again (R7). |

**Revised score: 21 PASS, 1 PASS-with-caveat (13), 0 FAIL.**

### 0.8 Recommended follow-ups (none blocking)

1. Bound the preflight wait (N1) — a one-argument change that keeps the 400 and removes the unbounded pre-header wait.
2. Scope or confirm the Anthropic thinking guard (N2).
3. Add the cross-operation leakage assertion that replaced the deleted placebos (N3) — I ran it and it passes, so it is a pure test addition.
4. Gate the legacy path on `body.get("tool_choice") is None` (N4).
5. Amend the review prompt's invariant 14 (drop "post-tool continuation") and invariant 7 (drop "padded") so they match the approved designs.
6. Decide the fate of the two untracked Task-10 files: commit and run with `--include-integration`, or delete.

---

## 1. Scope verification

| Item | Value |
|---|---|
| Repository | `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent` |
| Branch | `feat/explicit-per-turn-tool-contract-v1` |
| HEAD | `68b9527772462747d6698b2a2716488739a242c8` |
| `git merge-base base HEAD` | `0d6e8514a6f281dc414ad714277ba812a81e4d28` |
| Review range | `0d6e8514a..68b952777` |
| Review-only descendant | **None.** HEAD *is* `68b952777`; `68b952777..HEAD` is empty. The review prompt itself is untracked. |
| `git diff --check 0d6e8514a..68b952777` | exit 0, no whitespace defects |
| Changed files | 43 (`4110 +`, `83 -`) — 22 production/test Python + 2 docs + 19 tests |
| Gateway repo files in range | **None.** No path outside `hermes-agent/` appears. |
| Literal `main` | Not touched, not used as merge base, no Git state mutated. |

Commit list matches the prompt exactly (verified with `git log --oneline --reverse 0d6e8514a..68b952777`), all eleven, in order.

**Starting worktree state (preserved byte-for-byte):** 19 untracked paths (`.otto/`, `docs/assessments/`, `docs/handoffs/`, two 2026-08-12 design/plan files, fifteen `docs/reviews/*`). None read for content, modified, staged, or deleted. `git diff --stat` was empty at start and at finish. All probes were written to the session scratchpad **outside the repository** and are gone; nothing was added to or removed from the worktree except this review file.

**Gateway boundary documents — substitution recorded.** Both named paths are **absent** from the primary Gateway checkout (`otto-gateway/docs/superpowers/{specs,plans}/2026-08-15-model-selection-aware-tool-contract*.md` → `No such file or directory`). Per the prompt's instruction, I located the approved copies in that repository's existing `model-selection-aware-tool-contract` worktree and read them there:

```
/Users/coreyellis/code/github.com/cmetech/otto_app/otto-gateway/.worktrees/
    model-selection-aware-tool-contract/docs/superpowers/specs/2026-08-15-model-selection-aware-tool-contract-design.md   (426 lines, read)
    model-selection-aware-tool-contract/docs/superpowers/plans/2026-08-15-model-selection-aware-tool-contract.md          (366 lines, read)
```

Read-only. No Gateway file was modified, no Gateway Git state was touched, no unrelated Gateway change was inspected, and no live Gateway operation was run. The wire-boundary agreement this enabled is reported in §5 (invariants 7, 9, 12, 15) and §7.

**Unavailable / not exercised:** live OTTO Gateway, live providers, real credentials, deployed-release probes, Task 10 artifacts.

---

## 2. Verdict

### **DON'T SHIP**

**Single most important reason:** the branch changes the outbound request body of **every** request that enters through the API server — including requests that never opted into the feature. `_parse_api_tool_operation` returns a non-`None` operation context unconditionally, so `resolve_tool_choice` now emits `tool_choice` on every Chat Completions call, and on a tool-less agent it emits `tool_choice` with **no `tools` field at all** — a request shape the OpenAI Chat Completions schema rejects. This regresses traffic that has nothing to do with the contract, and the feature's stated rollback story ("callers stop selecting mandatory policy; no environment change needed") does not undo it.

The highest-**severity** defect is separate: the Anthropic Messages **streaming** route sends `X-Otto-Tool-Contract: v1` and then relays deltas and surfaces tool calls **without ever checking the echo** — the exact fail-closed guarantee this feature exists to provide.

This verdict covers Tasks 1–9 code readiness only. It is not a judgment on live coordinated release, which is unproven by construction (§10).

What *is* solid: the policy value objects, the request-scoped lifecycle, the post-tool `auto` transition, the authorization boundary, prompt-cache stability, and the chat-completions echo enforcement are genuinely correct and I verified them against running code, not tests.

---

## 3. Findings

Sorted by severity. `file:line` anchors are against `68b952777`.

| # | file:line | Sev | Invariant | Defect | Concrete failure scenario | Minimal fix |
|---|---|---|---|---|---|---|
| **F1** | `agent/chat_completion_helpers.py:3686` (`_open_anthropic_stream`), reached from `interruptible_streaming_api_call:2594` | **Critical** | 8, 10 | The Anthropic Messages **streaming** path opens `request_client.messages.stream(**final_kwargs)` and returns the live stream with **zero** contract enforcement. It never calls `contract_required`/`verify_stream_echo` and never delegates to the guarded `create_anthropic_message`. `anthropic_messages` is explicitly allowlisted in `otto_tool_contract._HEADER_CAPABLE_MODES:14`, so the v1 header *is* sent; `sanitize_anthropic_kwargs` preserves `extra_headers`. | An OTTO route on `api_mode="anthropic_messages"` with streaming enabled (`_disable_streaming` is `False` by default) sends `X-Otto-Tool-Contract: v1`. A Gateway build that does not support v1 answers with no echo. Hermes relays **every** text delta to the user callback and surfaces the response's `tool_use` block, which the loop then executes. No `otto_tool_contract_unavailable` is ever raised. The sibling non-streaming path (`create_anthropic_message`, `anthropic_adapter.py:3224`) refuses the identical response. | Inside `_open_anthropic_stream`, after `manager.__enter__()` and **before** returning, call `verify_stream_echo(stream, contract_required=contract_required(final_kwargs))`; wrap the `messages.stream(...)` call in `verify_exception_echo`. Add a regression test asserting no delta callback fires and no tool call is surfaced when the echo is absent. |
| **F2** | `gateway/platforms/api_server.py:1146` (`_parse_api_tool_operation`) + `agent/transports/base.py:57` (`resolve_tool_choice`) | **High** | 22 | `_parse_api_tool_operation` **always** returns a context: with no `tool_choice` in the body and no contract header it still builds `ToolOperationContext(policy=auto, call_role="primary")`. `resolve_tool_choice` returns `"auto"` for any non-`None` context and `ChatCompletionsTransport.build_kwargs` sets `api_kwargs["tool_choice"]` unconditionally — even when `tools` is falsy and no `tools` key is emitted. | `POST /v1/chat/completions` with `{"model":…,"messages":[…]}`, **no** `tool_choice`, **no** `X-Otto-Tool-Contract`, against a Hermes agent whose effective toolset is empty (`enabled_toolsets: []`, `--no-tools`, or every toolset disabled). Wire body becomes `{"model","messages","tool_choice":"auto"}` with **no `tools`** — invalid per the OpenAI Chat Completions schema and rejected by OpenAI and strict compatible servers. Even with tools present, every API-server request now carries a `tool_choice` field it did not carry on `0d6e8514a`, and every native-Gemini request gains a `toolConfig` block. `emit_tool_contract_event` also now fires on every API-server turn. This additionally violates the **Gateway** design's rollout clauses §15.3 ("deploy Hermes support **without enabling it by default for ordinary turns**") and §15.5 ("requests without an explicit selection continue using legacy behavior"). | In `_parse_api_tool_operation`, return `(None, None)` when `body.get("tool_choice") is None` **and** the contract header is absent. Defensively, in `resolve_tool_choice`, return `None` for `mode == "auto"` when `_tool_names(tools)` is empty. Regression test: a headerless, choice-less API request produces `"tool_choice" not in api_kwargs`. |
| **F3** | `agent/anthropic_adapter.py:2975-2982` reached via `agent/transports/anthropic.py:65` | **High** | 13 | `required`/`named` policy on an Anthropic Messages model is mapped to `tool_choice: {"type":"any"}` / `{"type":"tool",...}` **and** `thinking: {...}` is emitted in the same request when reasoning is configured. Anthropic documents these as mutually exclusive (extended thinking supports only `tool_choice` `auto`/`none`). The design (§8) explicitly names "a reasoning mode that cannot coexist with forced tool selection" as a fail-closed case; nothing checks it. | CLI/API `tool_choice: "required"` (or `/tool-choice required`) on a Claude model with reasoning enabled. `AnthropicTransport.build_kwargs` returns `tool_choice={'type':'any'}` **plus** `thinking={'type':'enabled','budget_tokens':16000}` (verified). Hermes dispatches; Anthropic returns a 400 `invalid_request_error`. The user sees a raw provider validation error instead of the typed `mandatory_tool_choice_not_supported`, and the failure surfaces only after a network round-trip. | In `AnthropicTransport.build_kwargs` (or `build_anthropic_kwargs`), raise `ToolChoicePolicyError("mandatory_tool_choice_not_supported", …)` when the resolved choice is `required`/named **and** an enabled `thinking` config would be emitted. Test asserts the raise for both `required` and `named`, and no raise for `auto`/`none`. |
| **F4** | `tui_gateway/server.py:6103` (`_reset_session_agent`); state written at `tui_gateway/methods_tools.py:21` | **High** | 19 | The TUI/desktop one-shot control lives in `session["tool_choice_control"]` and is **never removed by any code path in `tui_gateway/`**. `_reset_session_agent` is an in-place conversation boundary — it clears `session["history"]`, rebuilds the agent, and deliberately pops the *other* one-shot session overrides (`one_turn_model_restore`, `model_override`, `create_reasoning_override`, `create_service_tier_override`) — but leaves the pending tool policy intact. The CLI clears it in `new_session()` and the gateway clears it via `_CONVERSATION_SCOPED_STATE`; the TUI is the outlier. | TUI/desktop user runs `/tool-choice required --otto-v1`, changes their mind and toggles a toolset (which routes to `_reset_session_agent` via `methods_tools.py:1555`, clearing history and rebuilding the agent), then types an ordinary prompt. The first turn of the **new** conversation runs with `required` + `X-Otto-Tool-Contract: v1`. If the rebuilt agent no longer exposes a previously named tool, the turn dies with `mandatory_tool_choice_not_supported`. | `session.pop("tool_choice_control", None)` in `_reset_session_agent` and in any other in-place session-reset routine. Test: set the control, reset, assert `_consume_session_tool_choice(session) is None`. |
| **F5** | `gateway/platforms/api_server.py:6194-6198` (validation) → `:4497` (SSE render) | **Medium** | 6, 15 | `validate_tool_choice_policy` runs *inside* `_run_agent`, after the SSE response is already open. On the streaming surface its `ToolChoicePolicyError` is never mapped to the stable typed category — the `except ToolChoicePolicyError` handlers were added only to the non-streaming `_compute_completion`/`_compute_response`. | Identical request, two answers. Non-streaming `{"tool_choice":{"type":"function","function":{"name":"unavailable_tool_fixture"}}}` → **HTTP 400**, `code: "invalid_tool_choice"`. Same body with `"stream": true` → **HTTP 200**, SSE `error.type: "ToolChoicePolicyError"` (an internal Python class name), `hermes.error_code: "agent_error"`, and a contradictory `hermes.completed: true` alongside `failed: true`. | Validate the named choice **before** opening the SSE response (the effective catalog is known once the agent is created), or map `ToolChoicePolicyError` in `_write_sse_chat_completion`/the Responses streamer to `code=exc.code`, `completed=False`. |
| **F6** | `agent/anthropic_adapter.py:3250` | **Medium** | 10, 15 | `verify_exception_echo` sits in an `except Exception` that wraps both stream **creation** and stream **iteration**. Once the echo has already been verified at `:3224`, any later mid-stream transport failure re-enters it, finds no `.response` headers on the transport error, and is converted to terminal `otto_tool_contract_unavailable`. Gateway design §5.2 scopes the echo guarantee to "every successful response and every **typed Gateway error selected under v1**" — a client-side connection reset is neither, so requiring an echo on it over-applies the contract. | v1 operation on the Anthropic OTTO route; echo verified; connection reset after several events. The user gets "The selected gateway does not support the requested tool contract." — a permanent, non-retryable failure — for a transient network drop, and telemetry records `echo=False` for a response that *did* echo. | Verify the exception echo only around the `stream_fn(**stream_kwargs)` call (or short-circuit once the echo has been verified for that attempt). |
| **F7** | `agent/conversation_loop.py:5445`, `:5854` | **Medium** | 21 | The telemetry `echo` field is **derived, not observed**. On success it is `otto_contract_version == "v1"` (a tautology at that point); on failure it is `terminal_code != "otto_tool_contract_unavailable"`. | A `mandatory_tool_choice_not_supported` failure never reaches the network, yet its event reports `echo=True`. A `mandatory_tool_choice_not_supported` on a direct provider reports `echo=False`, indistinguishable from a real echo miss. The design's "response echo present: boolean" is therefore unusable for the one question it exists to answer. | Thread the observed echo outcome (`True`/`False`/`None` when no request was sent) from `verify_response_echo`/`verify_stream_echo` into the event. |
| **F8** | `agent/conversation_loop.py:1243` (`_tool_policy_allows_fallback`) | **Medium** | 14 | After a structured tool call, a **non-v1** `required`/`named` operation becomes `policy.mode == "auto"`, `otto_contract_version is None` — so provider/model fallback is re-enabled for the same logical operation. The branch's own test asserts this (`test_conversation_loop_tool_policy_fallback_is_rejected_for_mandatory_operation` → `_tool_policy_allows_fallback(post_tool) is True`), so it is deliberate, not accidental. | API caller sends `tool_choice: "required"` to a direct provider with a configured fallback chain. Initial call → required → model calls a tool → Hermes executes it → the post-tool continuation hits a 429 → `_try_activate_fallback()` now succeeds and the tool result is replayed to a **different provider/model** than the one that produced the call. Invariant 14 names post-tool continuation explicitly. (v1 operations are correctly protected — the version marker survives the transition.) | Add a frozen `mandatory: bool` (or keep the originating mode) on `ToolOperationContext` set at creation, and gate `_tool_policy_allows_fallback` on it for the whole operation — or state the divergence in the design and record it as accepted. |
| **F9** | `agent/tool_choice_policy.py:140` vs `agent/transports/base.py:50` | **Low** | 13 | One condition, two codes. "Named tool not in the effective catalog" raises `invalid_tool_choice` at the API layer and `mandatory_tool_choice_not_supported` at the transport layer. Invariant 13 assigns the unknown-name case to `mandatory_tool_choice_not_supported`; §14 of the design assigns `mandatory_tool_choice_not_supported` the meaning "transport/model cannot express the policy," which fits neither reading cleanly. | A named choice that resolves at parse time but disappears after effective-scope filtering produces a different code (and a different HTTP status mapping: 400 for `invalid_tool_choice`, 400 for `mandatory_tool_choice_not_supported`, 502 for the rest) than the same name rejected at the API layer. Clients cannot key on one code. | Pick one code for "named tool unavailable" and use it in both places; document which. |
| **F10** | `gateway/platforms/api_server.py:1130` | **Low** | 7 | `request.headers.get()` on aiohttp's `CIMultiDict` returns only the **first** of duplicate headers, so `X-Otto-Tool-Contract: v1` sent alongside a second `X-Otto-Tool-Contract: v2` enables v1 and silently ignores the conflicting value. | Caller-controlled opt-in with no privilege gain; recorded only because invariant 7 names "duplicate" as must-not-enable. **The padding half of invariant 7 is not a defect:** the Gateway design §5.1 specifies "the value is **trimmed** and compared to the exact allowlist," so Hermes's `.strip()` matches the other side of the wire exactly. Case-variant (`V1`), comma-joined (`v1,v1`) and unknown (`v2`) values all correctly 400. | Reject when `len(request.headers.getall("X-Otto-Tool-Contract", [])) > 1`. Amend invariant 7 to drop "padded", which contradicts the approved Gateway contract. |
| **F11** | `agent/anthropic_adapter.py:2977-2979` | **Low** | 12 | Under v1 on the Anthropic OTTO route, `none` policy **omits `tools` entirely and sends no `tool_choice`**. Gateway design §6 canonicalizes Anthropic Prohibited as `tool_choice: none`; a request with neither tools nor a choice matches that table's **Optional** row instead ("absent or `auto`"). The adapter's comment "Anthropic has no tool_choice 'none'" is also stale — the Messages API added it. | A v1 `none` turn is recorded by Gateway diagnostics as `tool policy: optional`, not `none`, so the two sides disagree on the canonical label for the same request. **No behavioral consequence:** with no tools declared, no tool can be called, and Gateway's decision guard is ineligible anyway (§6 requires "caller tools are present"). Hermes's own design §8 sanctions omission, so this is a boundary-vocabulary mismatch, not a policy break. | Either emit `tool_choice: {"type":"none"}` while keeping the tools declared, or add a line to the Hermes design noting that Anthropic `none` is observed as Optional-with-no-tools by the Gateway. Refresh the stale comment either way. |
| **F12** | (test integrity — see §6) | **Low** | — | Six placebo/overclaiming tests and one under-collecting mandated selector. | Detailed in §6. | Detailed in §6. |

---

## 4. Top-five reproductions

All probes ran from the implementation checkout with `.venv/bin/python`, used only sanitized synthetic fixtures and loopback servers, and were deleted afterwards.

### R1 — F1: the Anthropic streaming route never checks the echo (Critical)

Two facts, both mechanically verified.

**(a) The guarded sibling refuses; callbacks never fire.** Driving `create_anthropic_message` with a fake stream whose `response.headers` lack the echo:

```
GUARDED create_anthropic_message [no echo]    -> RAISED otto_tool_contract_unavailable, callbacks=[], closed=True
GUARDED create_anthropic_message [wrong echo] -> RAISED otto_tool_contract_unavailable, callbacks=[], closed=True
GUARDED create_anthropic_message [exact v1]   -> 'FINAL', callbacks=['on_response'], closed=False
```

**(b) The streaming path does not use it and has no guard of its own.** AST extraction of `_open_anthropic_stream` from `interruptible_streaming_api_call`:

```python
def _open_anthropic_stream(next_api_kwargs: dict[str, Any]):
    final_kwargs = dict(next_api_kwargs)
    sanitize_anthropic_kwargs(final_kwargs, log_prefix=getattr(agent, 'log_prefix', ''))
    reserve_provider_transport_attempt(agent, request_client)
    manager = request_client.messages.stream(**final_kwargs)
    _stream_context['manager'] = manager
    return manager.__enter__()
```

and symbol presence inside the enclosing function:

```
references 'verify_stream_echo'          : True   <- only inside the chat_completions _open_stream
references 'contract_required'           : True   <- same
references 'create_anthropic_message'    : False
references '_anthropic_messages_create'  : False
```

`grep` over the whole `agent/` tree confirms the only echo-verification sites in `chat_completion_helpers.py` are lines 529–541 (non-streaming OpenAI dispatch) and 3201–3207 (chat_completions streaming). `sanitize_anthropic_kwargs` strips only Responses-only keys, so `extra_headers` — and the v1 header — reach the wire. Wrong observable result: deltas and tool calls from an unverified response are delivered and executed.

### R2 — F2: `tool_choice` appears on the wire for a request that never asked for it (High)

Sanitized request, real `AIAgent`, real conversation loop, loopback HTTP server capturing the body:

```
API-server context for a plain request:
  ToolOperationContext(operation_id='f716969c…', policy=ToolChoicePolicy(mode='auto', name=None),
                       call_role='primary', otto_contract_version=None)
agent.tools -> []
WIRE keys: ['messages', 'model', 'tool_choice'] | tool_choice: 'auto' | has tools: False
```

The same builder with `attempt_context=None` (the pre-branch shape) emits `['messages','model']` — no `tool_choice`. The context here was produced by the real `_parse_api_tool_operation` from `{"model":"m","messages":[]}` with `headers={}`. Wrong observable result: an OpenAI-invalid request body on a request that opted into nothing.

### R3 — F3: Anthropic mandatory choice + extended thinking in one request (High)

`AnthropicTransport.build_kwargs(model="claude-sonnet-4-5-20250929", reasoning_config={"enabled": True, "effort": "high"}, attempt_context=…)`:

```
required  tool_choice={'type': 'any'}                       thinking={'type':'enabled','budget_tokens':16000}  tools=True
named     tool_choice={'type': 'tool', 'name': 'tool_call'}  thinking={'type':'enabled','budget_tokens':16000}  tools=True
none      tool_choice=None                                   thinking={'type':'enabled','budget_tokens':16000}  tools=False
auto      tool_choice={'type': 'auto'}                        thinking={'type':'enabled','budget_tokens':16000}  tools=True
```

Rows 1 and 2 are the documented-incompatible combination. Nothing raises; the request is dispatched. Wrong observable result: a provider 400 where the design mandates a pre-dispatch `mandatory_tool_choice_not_supported`.

### R4 — F5: the same rejection, two different answers (Medium)

Real aiohttp `TestServer` on `_handle_chat_completions`, `_create_agent` patched to an agent whose catalog is `{"tool_call"}`, `tool_choice` naming `unavailable_tool_fixture`:

```
NON-STREAM status: 400 {"error": {"message": "Invalid tool choice.", "type": "invalid_request_error",
                                  "param": null, "code": "invalid_tool_choice"}}

STREAM status: 200
  data: {… "delta": {"role": "assistant"} …}
  data: {… "finish_reason": "error",
         "error":  {"message": "Invalid tool choice.", "type": "ToolChoicePolicyError"},
         "hermes": {"completed": true, "partial": false, "failed": true,
                    "error": "Invalid tool choice.", "error_code": "agent_error"}}
  data: [DONE]
```

Wrong observable result: no stable typed category on the streaming surface, an internal class name as `error.type`, and `completed: true` on a failed turn.

### R5 — Positive control: what the contract gets right (no finding)

Recorded because a hostile review must state what it could **not** break. Real `AIAgent`, `provider="otto"`, loopback Gateway, `required` + v1.

**Echo enforcement, non-streaming chat completions** — three runs, varying only the response header:

```
ECHO none  -> POST requests: 1 | failed=True completed=False
              error_code='otto_tool_contract_unavailable'  final_response=''  model text leaked: False
ECHO wrong -> POST requests: 1 | identical
ECHO v1    -> POST requests: 1 | failed=False completed=True  final_response='SECRET-MODEL-TEXT'
```

Exactly one request, no retry, no fallback, suppressed model text never reaches the result envelope or the logs.

**Lifecycle + authorization**, with an unauthorized tool name injected on the first response:

```
executed tools: ['tool_fixture']            <- 'attacker_unauthorized_tool' never executed
  attempt 1: tool_choice='required' role=primary   contract=v1
  attempt 2: tool_choice='required' role=primary   contract=v1   <- rejected call did NOT advance lifecycle
  attempt 3: tool_choice='auto'     role=post_tool contract=v1   <- only a VALID call derives post-tool auto
```

**Immutability and the role allowlist:**

```
policy after caller mutates the source dict: ToolChoicePolicy(mode='named', name='tool_call')   <- unchanged
set policy / call_role / otto_contract_version / policy.mode -> FrozenInstanceError (all four)
ToolChoicePolicy(mode='bogus')            -> invalid_tool_choice
ToolChoicePolicy(mode='required', name=…) -> invalid_tool_choice
ToolOperationContext(call_role='attacker')-> ValueError: call_role is not allowlisted
ToolOperationContext(otto_…='v2')         -> ValueError: unsupported OTTO tool contract version
otto_headers role=primary/post_tool       -> {'X-Otto-Tool-Contract': 'v1', 'X-Otto-Call-Role': <role>}
otto_headers role=title/compression/auxiliary/correction -> RAISE otto_tool_contract_unavailable
```

---

## 5. Invariant matrix

No omitted rows. "Guard" names the assertion or probe that fails if the invariant regresses.

| # | Invariant | Verdict | Production source | Evidence / guard |
|---|---|---|---|---|
| 1 | Policy only from explicit structured control; exactly four modes; prose never creates policy | **PASS** | `agent/tool_choice_policy.py:112` (only request-side producer), `agent/tool_choice_control.py:74` (only front-end producer) | Probe: every non-supported shape (`"REQUIRED"`, `{"type":"any"}`, `["required"]`, `1`, `True`, `""`, `{}`) raises `invalid_tool_choice`. No code in the range inspects prompt text for intent. Guard: `tests/agent/test_tool_choice_policy.py` |
| 2 | Immutable, request-scoped; no env var, global, agent field, session row, prior message, prompt, tool def, or cached-agent property can activate it | **PASS** | frozen dataclasses `tool_choice_policy.py:38,56`; `current_attempt_context` is a `run_conversation` local (`conversation_loop.py:1298`) | Probe R5: four `FrozenInstanceError`s; caller mutation of the source dict does not change the policy. No `agent.<attr> =` assignment for policy anywhere in the range; no env var added |
| 3 | Current attempt's `tool_choice` is the source of truth; a valid structured call immediately derives post-tool `auto` retaining the v1 operation | **PASS** | `conversation_loop.py:6487` (`after_structured_tool_call`, placed after invalid-name filtering) | Probe R5 lifecycle capture (required→required→auto/post_tool, v1 throughout). Guard: `test_conversation_loop_tool_policy_initial_and_post_tool_lifecycle` |
| 4 | Same-network-attempt retries reuse the same immutable context; unrelated/auxiliary/fallback attempts start fresh | **PASS** | loop-local reuse; `build_api_kwargs(..., attempt_context=None)` default (`chat_completion_helpers.py:1153`); explicit `attempt_context=None` at `:2299`, `:2419` | `test_..._network_retry_reuses_same_context` asserts object **identity** (`contexts[0] is context`); `test_iteration_summary_explicitly_uses_fresh_automatic_context` asserts `[None]` |
| 5 | Terminal paths clear; two concurrent operations on one cached agent cannot cross | **PASS** | function-local context + per-call closures (`conversation_loop.py:1304,1309`) | `test_..._concurrent_builds_are_isolated` (threading.Barrier, distinct results). Nothing is stored on the agent, so nothing survives the frame. See §6 — the `agent.__dict__` assertions are tautological, but the production code is genuinely stateless here |
| 6 | Parsing accepts supported forms, rejects malformed/unknown with a stable typed category; idempotency includes normalized policy + exact version, not arbitrary headers | **FAIL (partial)** | `api_server.py:1120-1152`, `:4217-4232`, `:5401-5424` | Parsing and idempotency PASS (probe R4 non-streaming; `test_idempotency_distinguishes_tool_choice_and_contract_version`; key lists are explicit, no header material). **Fails on the streaming surface** — F5 / R4 |
| 7 | Only exact inbound `v1` creates trusted context; never blindly forwarded, reflected, or enabled for absent/padded/case-variant/duplicate/comma-joined/unsupported values | **PASS with caveat** | `api_server.py:1130`; outbound generated at `otto_tool_contract.py:29` | Probe: `V1` → 400, `v1,v1` → 400, `v2` → 400, absent/empty → no v1, inbound `X-Otto-Call-Role` ignored. `add_otto_request_headers:59-64` strips any caller-supplied contract/role keys from `extra_headers` before merging its own. **The "padded" clause of this invariant is wrong, not the code** — Gateway design §5.1 mandates that the value be trimmed before the allowlist comparison, and Hermes matches. Remaining caveat: duplicate headers — F10 |
| 8 | Hermes generates the exact static v1 header from validated context; direct providers get none; a header-incapable transport fails before dispatch | **PASS** | `otto_tool_contract.py:29,41`; `chat_completion_helpers.py:533-536` | Probe: role allowlist enforced; `provider="openai"` → no headers; `("otto","bedrock_converse")` → `otto_tool_contract_unavailable`; a client without `with_raw_response` raises before any `create()` (`ordinary_calls == []`). Guard: `test_raw_response_tool_contract_*` |
| 9 | `X-Otto-Call-Role` is allowlisted diagnostic metadata only | **PASS** | `otto_tool_contract.py:33-38` | Recomputed per request from context; only `primary`/`post_tool` permitted, everything else raises; no production code reads the role back to authorize, select a model, alter policy, or gate execution (`grep` over the range). Inbound role header has no effect (probe). **Boundary-checked:** Gateway design §13 accepts `primary`, `post_tool`, `title`, `compression`, `auxiliary` and maps anything else to `unknown`; Hermes emits a strict subset (`primary`/`post_tool` only) and refuses to emit the rest under v1 — stricter than required, never in conflict |
| 10 | Echo verified before parsing/returning non-streaming content, before the first streaming delta, and before executing any surfaced tool call | **FAIL** | `chat_completion_helpers.py:529,3201`; `codex_runtime.py:1287`; `anthropic_adapter.py:3213` | chat_completions (both modes) and codex_responses streaming PASS — probe R5 shows exactly one request, terminal code, no leaked text. **Anthropic Messages streaming is unguarded** — F1 / R1 |
| 11 | Chat Completions maps required/named/none natively; Responses/Codex uses per-attempt policy instead of hard-coded `auto` | **PASS** | `transports/base.py:57-63`; `transports/codex.py:432-436,494-495` | Probe: `required`→`'required'`, named→`{"type":"function","function":{"name":…}}`, none→`'none'`; Responses named→`{"type":"function","name":…}`. `grep` confirms no hard-coded `"auto"` remains in any transport. Guard: `test_responses_tool_policy_mapping_replaces_hard_coded_auto` |
| 12 | Anthropic any/tool/omit; Gemini ANY / ANY+name / NONE; Bedrock any/tool/omit-or-equivalent | **PASS with caveat** | `anthropic_adapter.py:2972-2982`; `gemini_native_adapter.py:442-457` (reached through the chat_completions dialect); `bedrock_adapter.py:1074-1105` | Probe R3 + `tests/agent/test_transports.py` (`{"type":"any"}`, `{"type":"tool",...}`, tools omitted for `none`; `{"any":{}}` / `{"tool":{"name":…}}` / no `toolConfig`). **Boundary-checked:** Gateway design §6 canonicalizes OpenAI `required`/named-`function`/`none` and Anthropic `any`/named-`tool`/`none`, and §6 explicitly warns that "OpenAI named choices arrive with wire type `function`" must stay visible to a `tool`-recognizing implementation — Hermes emits the correct wire type per surface on both. Caveat: Anthropic `none` is canonicalized differently by the two sides — F11. Coverage gap in §6: Gemini is proven only for `named` and only at `build_gemini_request`; Anthropic `auto` is untested |
| 13 | Required/named with no tools, unknown name, incompatible mode, or unsupported model/transport fails as `mandatory_tool_choice_not_supported`; never strip, switch provider/model, or route through `auto` | **FAIL (partial)** | `transports/base.py:44-55`; `bedrock_adapter.py:1094-1102` | No-tools, name-missing-after-filtering, and unsupported-Bedrock-model all raise correctly (`test_tool_policy_mandatory_capability_failures_are_explicit`, `test_tool_policy_rejects_mandatory_choice_for_unsupported_bedrock_model`). **The Anthropic reasoning incompatibility the design names explicitly is unchecked** — F3 / R3. Code drift — F9 |
| 14 | Explicit model authoritative for initial call, retry, and post-tool continuation; Gateway protocol/echo failures never activate fallback | **FAIL (partial)** | `conversation_loop.py:1243`, all 12 fallback call sites rewritten to the gated wrappers | Protocol/echo → no fallback, no request dump, terminal (probe R5; `test_gateway_protocol_error_is_terminal_without_fallback` asserts `fallback_attempts == []`). **Non-v1 mandatory post-tool continuation re-enables fallback** — F8 |
| 15 | The five codes are terminal, allowlisted, protocol-native, and privacy-safe in streaming and non-streaming envelopes | **PASS with caveat** | `error_classifier.py:146-160,748-758`; `api_server.py:1109-1119,4259,4467,5440` | Probe R5: terminal, no retry, `"private upstream detail"` absent from both the result and `caplog`. API tests cover 400/502, SSE, and Responses `response.failed`. **Boundary-checked:** Gateway design §12 assigns `unsupported_tool_contract_version`→400, `selected_model_tool_protocol_failed`→502, `selected_model_tool_result_provenance_failed`→502; `_tool_contract_result_error` maps all three identically. `mandatory_tool_choice_not_supported` is a client error in Gateway §5.3 and Hermes maps it to 400 — consistent, though the Gateway's §12 table omits it. `otto_tool_contract_unavailable` is Hermes-only (the caller's own no-echo verdict, never emitted by Gateway) and 502 is defensible. Caveat: F5 (streaming `invalid_tool_choice` drift, `completed: true`) |
| 16 | Hermes remains the tool host/executor; headers and policy grant no authorization; outer and inner tools validated against the effective scoped catalog | **PASS** | unchanged authorization surface | Mandatory check `git diff --quiet 0d6e8514a..68b952777 -- agent/prompt_builder.py toolsets.py model_tools.py tools/registry.py` → **exit 0** (byte-unchanged). Probe R5: an unauthorized tool name under `required`+`v1` is error-resulted, never executed, and does not advance the lifecycle |
| 17 | Native call/result pairing and strict role alternation hold; no synthetic user message; transition happens before execution without separating a call from its result | **PASS** | `conversation_loop.py:6466-6492` | The transition sits **after** the assistant message and the invalid-call error results are appended and **before** persistence/execution. No `messages.append({"role": "user"…})` anywhere in the range. Probe R5 completed a full tool round-trip with intact pairing |
| 18 | System prompt and stable ordered tool prefix byte-identical across initial and post-tool; contract/role/operation/policy/headers absent from the cache key | **PASS** | `chat_completions.py:492,694` and `:836` — `tool_choice` is attached **after** `_add_prompt_cache_key` on both builder paths | Lifecycle test compares captured wire `messages[0]` and `tools` between the initial and post-tool requests; `test_prompt_cache_key_ignores_operation_policy_and_identity` asserts equal `prompt_cache_key` for `required` vs `post_tool` contexts with different operation IDs |
| 19 | One-shot controls front-end/session-owned, visible, consumed by exactly one accepted operation, cleared on success/cancel/error/new/reset/abandon, no transcript text | **FAIL** | `cli.py:8123,13995`; `gateway/run.py:2386,2389`; `tui_gateway/methods_prompt.py:16` | CLI and gateway PASS (clear on `new_session` / `_CONVERSATION_SCOPED_STATE`; consume-once proven). **TUI never clears** — F4. Secondary: the TUI consumes at prompt acceptance (`methods_prompt.py:294`) *before* the agent-readiness gate, so an abandoned submission silently discards the policy rather than preserving it |
| 20 | No `X-Hermes-Session-Id` ↔ `X-Session-Id` equivalence, propagation, or use as provenance | **PASS** | `otto_tool_contract.py:35-38` emits exactly two headers | Mandatory scan `git diff … ':(exclude)docs/**' \| rg 'Authorization:\|Bearer \|X-Session-Id\|X-Hermes-Session-Id'` → **exit 1, no match**. No session identifier appears in any generated header or diagnostic |
| 21 | Diagnostics bounded: only correlation, allowlisted role, model status, policy, version, transport, booleans, allowlisted terminal code, retry/fallback decision | **PASS with caveat** | `agent/tool_contract_telemetry.py:32-72` | Every field is a closed enum with a safe fallback, a boolean, or a truncated sha256; `policy` emits the **mode only** (a named tool's name — potentially a private connector identifier — never leaves). No prompts, arguments, schemas, results, credentials, raw headers, or session IDs are reachable. `emit` is fire-and-forget and swallows all exceptions. Caveat: F7 (`echo` is derived, not observed) |
| 22 | Contract absence and ordinary `auto` preserve legacy behavior; provider kwargs, reasoning, stable tools, retry/compression/fallback, streaming callbacks, persistence, and output unchanged | **FAIL** | `api_server.py:1146` + `transports/base.py:57` | **Every** API-server Chat Completions request now carries `tool_choice`; a tool-less one carries it with no `tools` — F2 / R2. Everything else in the invariant holds: reasoning settings, service tier, max tokens, temperature, and provider kwargs are untouched by the policy path, and CLI/TUI/gateway turns without a control still pass `None` |

**Score: 14 PASS, 3 PASS-with-caveat, 5 FAIL (three of them partial).**

---

## 6. Test-integrity assessment

**Real composition coverage (credit where due).** `tests/agent/test_tool_choice_lifecycle.py` is genuine end-to-end work: a real `ThreadingHTTPServer`, a real `AIAgent`, the real `run_conversation`, and assertions against the **captured wire payload and headers** — not a builder's return value. It proves the initial→post-tool transition, header roles, prompt/tool stability across attempts, retry context identity, and terminal protocol behavior with the production fallback wrappers patched at the agent boundary. `tests/gateway/test_api_tool_choice_contract.py` drives real aiohttp routes through the real handlers for parse, idempotency, and all four error-render surfaces.

**Pure-unit / mocked coverage that reads stronger than it is.**

- `tests/agent/test_transports.py` and `test_chat_completion_helpers.py` stop at builder return values and a `_CapturingTransport`. Correct as far as they go; they prove no dispatch behavior.
- `tests/agent/test_otto_tool_contract.py` is almost entirely `SimpleNamespace` fakes. Two tests do reach `_dispatch_nonstreaming_api_request`, which is real wiring.
- Gemini is proven only by calling `build_gemini_request` with a hand-written `tool_choice` dict, and only for `named`. Nothing proves the chat-completions dialect value actually reaches a native Gemini request, and `required`/`none`/`auto` on Gemini are untested. Anthropic `auto` is untested.

**Tests that cannot fail (placebos).**

- `tests/gateway/test_tool_choice_control.py::test_gateway_conversation_boundary_can_drop_pending_control` pops a key from a plain local dict and asserts it is gone. It executes **no production code** — not `_CONVERSATION_SCOPED_STATE`, not any reset routine. It is the only "cleared on reset" coverage the gateway has.
- `tests/tui_gateway/test_tool_choice_control.py::test_tui_prompt_handler_installs_tool_choice_consumer_in_server_namespace` asserts a function returns `None` for `{}`.
- The repeated `assert "tool_operation_context" not in agent.__dict__` (five lifecycle tests) can never fail: no code path ever sets that attribute under any name. It is a guard against a bug shape that was never written, not against leakage.

**Fakes that hide the seam they exist to prove.** `tests/gateway/test_tool_choice_control.py::_Runner._session_key_for_source` returns `source.session_key` verbatim. The real `GatewayRunner._session_key_for_source` (`gateway/run.py:6614`) resolves through `session_store._generate_session_key(source)` with profile namespacing, while consumption uses `ctx.session_key` (`gateway/run.py:5328`). **Nothing proves those two derivations agree.** If they diverge, the gateway `/tool-choice` control is silently inert — the policy is set, never consumed, and lingers until the conversation boundary drops it. Add a test that exercises both the command handler and `TurnRunner` against one real session source.

**Mandated selector under-collects.** `pytest -q tests/agent/test_transports.py -k 'tool_choice or required or named or none'` collects **4 of 11** tests — the mapping tests are named `*_tool_policy_*`, not `*_tool_choice_*`. Running the whole file gives `11 passed`. Recorded per the prompt's instruction about selectors that collect less than intended; not concealed by substituting a narrower or broader command.

### Highest-risk behavior the suite does not truly prove

**That the OTTO v1 echo is verified before the first byte reaches a consumer, on a real streaming transport.** The only test claiming it — `test_echo_before_first_stream_callback_or_iteration` — calls `verify_stream_echo` directly on a `SimpleNamespace` and asserts it raises and closes. It never touches a transport, a callback, a queue, or an SSE frame. Every streaming call site could drop its `verify_stream_echo` line and that test would still pass. **That is exactly the defect F1 is:** one of the three streaming call sites was never written, and no test noticed. Any fix must be guarded by a test that drives the real streaming entry point and asserts the delta callback was never invoked.

---

## 7. What I verified safe, and why

- **Request scope.** `_parse_api_tool_operation` builds a fresh `ToolOperationContext` with a fresh `uuid4` operation ID per request; `run_conversation` holds it in a local. There is no module global, no `ContextVar`, no agent attribute, no SessionDB column, and no environment variable in the entire range. Two sequential API requests get different operation IDs and the second is `auto`/no-v1 even when the first was `required`/v1 (asserted, and reproduced).
- **Concurrency on a cached agent.** The context is a `run_conversation` local and the two fallback wrappers are per-call closures over it, so two concurrent turns on one cached `AIAgent` cannot observe or overwrite each other's policy. The barrier test confirms it; more importantly, there is no shared mutable location for it to leak through.
- **Immutability.** Both dataclasses are `frozen=True` with `__post_init__` validation. `parse_tool_choice_request` copies the tool name out as a `str`, so mutating the caller's dict afterwards cannot change the policy. All four mutation attempts raise `FrozenInstanceError`.
- **Provider mappings.** I inspected the final native object for every policy on all five transports rather than trusting the tests: Chat Completions `required`/function-object/`none`; Responses `required`/`{"type":"function","name":…}`/`none`; Anthropic `{"type":"any"}`/`{"type":"tool",…}`/tools popped; Gemini `ANY`/`ANY`+`allowedFunctionNames`/`NONE` via the chat-completions dialect; Bedrock `{"any":{}}`/`{"tool":{"name":…}}`/`toolConfig` omitted. `none` genuinely omits or natively disables tools on every transport, and no default builder branch re-adds them (verified by reading past each mapping site to the end of the builder).
- **Echo timing on chat completions.** Both the non-streaming raw-response path and the streaming path verify before anything is parsed, relayed, or callback-delivered, and `parse_verified_raw_response` releases the raw response in a `finally`. A missing echo produces exactly **one** request — no retry occurred, which I confirmed by counting requests at the server rather than by reading the retry code. `OttoToolContractError` is a `ValueError`, so it matches none of the streaming worker's retry predicates (timeout / connection / parse / empty-stream) and cannot be retried there either.
- **Post-tool transition.** It is placed after invalid tool names are error-resulted and stripped, so a response containing **only** unauthorized calls does not advance the lifecycle — the next attempt is still `required`. Reproduced with an injected unauthorized name.
- **Selected-model authority under protocol failure.** All twelve `_try_activate_fallback`/`_has_pending_fallback` call sites in `conversation_loop.py` now route through the gated wrappers — I grepped for survivors and found none. Contract errors additionally suppress `_dump_api_request_debug`, the provider/endpoint/model `vprint` lines, and the body-details dump, so the terminal path cannot leak the request or the provider body.
- **Authorization.** The four authorization-critical files are byte-unchanged in the range (mandatory `git diff --quiet` returned exit 0). Neither the header, the contract version, the call role, nor a named policy is consulted at any execution gate; `required` still means "some authorized tool," enforced by the pre-existing `agent.valid_tool_names` filter.
- **Auxiliary isolation.** `build_api_kwargs`'s `attempt_context` is keyword-only with default `None`, so every pre-existing caller — title, compression, summaries, probes, delegation, fallback initialization, the whole of `auxiliary_client.py` — gets `None` without modification. `handle_max_iterations` passes `None` explicitly at both Codex sites. `otto_headers` additionally raises for any non-`primary`/`post_tool` role, so an auxiliary context could not emit v1 even if one were mistakenly constructed.
- **Cache stability.** `_add_prompt_cache_key` runs before `tool_choice` is attached on both chat-completions builder paths, so neither the policy nor the headers enter the key. The lifecycle test compares the actual captured system message and serialized tool list between the initial and post-tool requests.
- **Privacy.** Every message on the new error and telemetry paths is a static constant; `TOOL_CONTRACT_ERROR_MESSAGES` deliberately replaces the upstream body text, and the probe confirmed a planted `"private upstream detail must not survive"` string reaches neither the result nor `caplog`. The telemetry event emits the policy **mode** only — a named tool's name never leaves the process. This satisfies Gateway §13's mirror requirement that neither side use tool names or header values as metric labels.
- **Wire-boundary agreement.** Having read the approved Gateway design (§1), I checked Hermes against it rather than only against the Hermes design. The header spelling and the trim-then-exact-match rule, the echo semantics, the OpenAI/Anthropic canonical policy mappings — including the `function`-vs-`tool` named-spelling hazard the Gateway design calls out explicitly in §6 — the `X-Otto-Call-Role` allowlist and its inertness, and the three shared error codes with their HTTP classes all match. Hermes does not invent a different header, code, lifecycle, or payload meaning. Two divergences are reported as F11 (Anthropic `none` canonical label) and F2 (the ordinary-turn rollout clause).

---

## 8. Verification evidence

All commands run from the implementation checkout with the checkout's configured `.venv` (Python 3.11.15). No command was silently narrowed.

```
git status --short --branch                  exit 0   feat/explicit-per-turn-tool-contract-v1 + 19 untracked (unchanged)
git branch --show-current                    exit 0   feat/explicit-per-turn-tool-contract-v1
git merge-base base HEAD                     exit 0   0d6e8514a6f281dc414ad714277ba812a81e4d28
git rev-parse HEAD                           exit 0   68b9527772462747d6698b2a2716488739a242c8
git merge-base --is-ancestor 68b952777 HEAD  exit 0   (HEAD == 68b952777; 68b952777..HEAD empty)
git log --oneline --reverse 0d6e8514a..68b952777   exit 0   11 commits, exact match with the prompt
git diff --check 0d6e8514a..68b952777        exit 0   no whitespace defects
git diff --name-status 0d6e8514a..68b952777  exit 0   43 files, no Gateway path
```

Focused suites:

```
pytest -q tests/agent/test_tool_choice_policy.py tests/agent/test_tool_choice_lifecycle.py \
          tests/agent/test_otto_tool_contract.py tests/gateway/test_api_tool_choice_contract.py
    -> 55 passed, 21 warnings in 14.59s          exit 0

pytest -q tests/agent/test_transports.py -k 'tool_choice or required or named or none'
    -> 4 passed, 7 deselected in 0.46s           exit 0
    -> NOTE: selector collects 4 of 11. Whole file: 11 passed in 1.78s, exit 0.

pytest -q tests/agent -k 'otto_tool_contract or raw_response or echo or selected_model or no_fallback'
    -> 49 passed, 3 skipped, 4164 deselected     exit 0
    -> 3 skips are pre-existing and unrelated: tests/agent/test_auxiliary_relay.py,
       test_relay_llm.py, test_relay_tools.py — all "could not import 'nemo_relay'".

pytest -q tests/gateway/test_api_server.py tests/gateway/test_api_server_runs.py \
          tests/test_lazy_session_regressions.py tests/test_tui_gateway_server.py \
          -k 'tool_choice or protocol or stream or error or prompt_submit'
    -> 88 passed, 555 deselected, 16 warnings    exit 0

pytest -q tests/agent/test_tool_contract_telemetry.py tests/cli/test_tool_choice_control.py \
          tests/gateway/test_tool_choice_control.py tests/tui_gateway/test_tool_choice_control.py
    -> 12 passed, 5 warnings                     exit 0

python -m compileall -q agent gateway hermes_cli tui_gateway   exit 0
git diff --check                                                exit 0
```

Canonical full suite:

```
scripts/run_tests.sh -q
    === Summary: 2826 files, 33450 tests passed, 1 failed (100% complete) in 1046.2s (14 workers) ===
    FULL_SUITE_EXIT=1

  1 HARD FAILURE (failed on attempt 1 AND on the runner's built-in retry):
    tests/plugins/workflow/test_performance_bounds.py
      ::test_resolution_wait_pre_due_sweeps_append_nothing_and_do_not_hot_loop
      assert (18.294019 - 14.635716) < 2.0     <- a CPU process_time budget

  3 FLAKY files (failed attempt 1, passed on retry — the runner flags these):
    tests/test_tui_gateway_server.py
      ::test_run_prompt_submit_requeues_all_unstarted_notifications_with_real_threading
    tests/plugins/workflow/test_scheduled_runs.py
    tests/test_managed_runtime_resolution.py
```

Isolated re-runs of all four, with no competing load:

```
scripts/run_tests.sh tests/plugins/workflow/test_performance_bounds.py  -> 33 tests passed,  0 failed (40.0s)
scripts/run_tests.sh tests/test_tui_gateway_server.py                   -> 517 tests passed, 0 failed (21.0s)  [x2]
scripts/run_tests.sh tests/plugins/workflow/test_scheduled_runs.py      -> 47 tests passed,  0 failed (109.1s)
scripts/run_tests.sh tests/test_managed_runtime_resolution.py           -> 8 tests passed,   0 failed (54.5s)
```

**Honest attribution of the one failure and three flakes.** All four are wall-clock/CPU-budget or real-threading timing tests and all four pass clean in isolation. I was running probes and focused pytest invocations concurrently with the full suite, which added CPU contention — I consider that the most likely cause and I am not claiming a clean full-suite run I did not observe. None of the four exercises the tool-contract code paths, with one caveat worth a follow-up: the flaky `test_run_prompt_submit_requeues_all_unstarted_notifications_with_real_threading` lives in a file whose subject, `_run_prompt_submit`, **was** modified by this branch (a keyword-only parameter guarded by signature introspection). It passed on retry and twice in isolation, and the change cannot alter notification requeue timing, but a maintainer should re-run the suite uncontended before treating the branch as green.

Gateway boundary-document location (read-only, no mutation):

```
ls .../otto-gateway/docs/superpowers/specs/2026-08-15-model-selection-aware-tool-contract-design.md   -> No such file or directory
ls .../otto-gateway/docs/superpowers/plans/2026-08-15-model-selection-aware-tool-contract.md          -> No such file or directory
find .../otto_app -name '2026-08-15-model-selection-aware-tool-contract*'
    -> .../otto-gateway/.worktrees/model-selection-aware-tool-contract/docs/superpowers/specs/…-design.md   (426 lines, read)
    -> .../otto-gateway/.worktrees/model-selection-aware-tool-contract/docs/superpowers/plans/….md          (366 lines, read)
git -C <that worktree> log --oneline -3   -> 457f247, 85c2f88, d8a606c   (inspected, not modified)
grep -c '^- \[x\]' <gateway plan> -> 0        grep -c '^- \[ \]' <gateway plan> -> 58
```

Privacy and stable-surface checks:

```
git diff --quiet 0d6e8514a..68b952777 -- agent/prompt_builder.py toolsets.py model_tools.py tools/registry.py
    -> exit 0   (all four byte-unchanged)

git diff 0d6e8514a..68b952777 -- . ':(exclude)docs/**' \
  | rg -n 'Authorization:|Bearer |X-Session-Id|X-Hermes-Session-Id'
    -> exit 1   (no match — recorded as no match, not as an error)
```

Final worktree state — identical to the start:

```
git status --short --branch
    ## feat/explicit-per-turn-tool-contract-v1
    ?? .otto/ … (the same 19 untracked paths, unread and unmodified)
git diff --stat
    (empty)
```

No probe file was written inside the repository; all lived in the session scratchpad and were removed. No production file or documentation file was edited. No commit, push, merge, tag, release, deploy, or cross-repository operation was performed, and no live Gateway, provider, or credential-bearing command was run.

---

## 9. Required remediation before ship

Ordered. Each item names the focused regression test the fix needs.

1. **F1 (Critical) — guard the Anthropic streaming route.** In `_open_anthropic_stream` (`agent/chat_completion_helpers.py:3686`), compute `contract_required(final_kwargs)`, wrap `messages.stream(...)` in `verify_exception_echo`, and call `verify_stream_echo` on the entered stream **before returning it** — mirroring `_open_stream` at `:3201`. *Test:* drive `interruptible_streaming_api_call` with `api_mode="anthropic_messages"`, a v1 context, and a fake stream lacking the echo; assert `OttoToolContractError`, zero delta callbacks, zero surfaced tool calls, and that the stream was closed. Then delete the guard line and confirm the test fails — the current `test_echo_before_first_stream_callback_or_iteration` would not.
2. **F2 (High) — stop injecting `tool_choice` into requests that did not ask for it.** `_parse_api_tool_operation` returns `(None, None)` when `body.get("tool_choice") is None` and the contract header is absent; `resolve_tool_choice` returns `None` for `auto` with an empty tool catalog. *Test:* a headerless, choice-less `/v1/chat/completions` against a tool-less agent produces api_kwargs with no `tool_choice` key; an explicit `"auto"` still produces one. Note that `test_next_chat_request_gets_fresh_auto_no_v1_context` currently **asserts the defective behavior** and must be updated.
3. **F3 (High) — fail closed on Anthropic mandatory-choice + extended thinking.** Raise `mandatory_tool_choice_not_supported` before dispatch. *Test:* `AnthropicTransport.build_kwargs` with `required` and with `named` plus an enabled reasoning config raises with that code; `auto` and `none` do not.
4. **F4 (High) — clear the TUI one-shot control on every conversation boundary.** `session.pop("tool_choice_control", None)` in `_reset_session_agent` and any sibling in-place reset. *Test:* set the control, run the reset, assert `_consume_session_tool_choice(session) is None`.
5. **F5 (Medium) — make the streaming surface return the same typed category as the non-streaming one.** Validate the named choice before the SSE response opens, or map `ToolChoicePolicyError` in both streamers to `code=exc.code` with `completed=False`. *Test:* the streaming variant of `test_unknown_named_tool_fails_after_effective_catalog_is_known` asserts `invalid_tool_choice` and `completed: false`.
6. **F6 (Medium) — scope `verify_exception_echo` to stream creation** in `anthropic_adapter.py:3250` so a post-echo mid-stream error is not relabelled `otto_tool_contract_unavailable`. *Test:* a stream that echoes v1 and then raises `ConnectionError` mid-iteration surfaces the transport error, not the contract error.
7. **F7 (Medium) — report the observed echo, not the requested version.** *Test:* a `mandatory_tool_choice_not_supported` event carries `echo=None` (or `False` with a distinct reason), never `True`.
8. **F8 (Medium) — decide and encode post-tool model authority for non-v1 mandatory operations.** Either mark the operation mandatory for its whole lifetime, or amend the design and the invariant. *Test:* whichever behavior is chosen, assert it directly instead of asserting `_tool_policy_allows_fallback(post_tool) is True` in isolation.
9. **F9 / F10 / F11 / F12 (Low)** — unify the unknown-named-tool code; reject duplicate contract headers (and drop "padded" from invariant 7, which contradicts Gateway §5.1); reconcile the Anthropic `none` canonicalization with Gateway §6 or document the divergence; and replace the three placebo tests plus the faked `_session_key_for_source` with tests that exercise production code.

---

## 10. Release-evidence gaps — UNPROVEN, not defects

These are absent by design per the plan's cross-repository ordering. None is a code finding; none should be counted as a pass.

1. **Deployed Gateway commit/version — UNPROVEN.** No Gateway build was contacted. Whether any deployed Gateway echoes `X-Otto-Tool-Contract: v1` at all is unverified from Hermes's side. The approved Gateway plan I read carries **0 checked boxes out of 58**, while its worktree's Git log shows implementation and review-followup commits (`457f247 fix: classify adjacent provenance refusals`). The checkboxes are therefore unmaintained and cannot be used as a completion signal in either direction — the deployment state must be confirmed by the Gateway owner, not inferred.
2. **Direct exact-v1 probes without Hermes — UNPROVEN.** Step 2 of both deployment orders ("directly probe v1 echo and typed errors" across the OpenAI, Anthropic and Ollama surfaces) has produced no artifact in this range.
3. **Gateway-side wire-shape agreement — PARTLY VERIFIED (documents), UNPROVEN (running code).** I read the approved Gateway design and plan from the worktree (§1) and checked Hermes against them statically. **Agreements confirmed:** the header name and exact-`v1`-after-trim rule (§5.1), the echo-before-accepting-content model (§5.2, §15), the canonical policy mappings for OpenAI and Anthropic including the `function`-vs-`tool` named-spelling hazard (§6), the `X-Otto-Call-Role` allowlist and its behavioral inertness (§13), and the three shared error codes with their HTTP classes (§12). **Disagreements found:** Anthropic `none` canonicalization (F11), and the ordinary-turn rollout clause (§15.3/§15.5) that F2 breaks. **Still unproven:** that the running Gateway behaves as its own document says.
4. **Real provider/model behavior — UNPROVEN.** Every provider mapping is verified against the request object Hermes builds, never against a provider's response. In particular F3's Anthropic incompatibility is asserted from Anthropic's documented constraint, not from an observed 400.
5. **Ollama v1 surface — NOT IMPLEMENTED IN HERMES, and not implementable through the current transports.** Both plans list it as an acceptance item (Hermes plan §16.11: "Gateway Ollama `/api/chat` extension mapping is covered; `/api/generate` mandatory selection fails before dispatch"). Hermes has **no Ollama-native api_mode** — Ollama is reached through the OpenAI-compat `chat_completions` surface, and `_HEADER_CAPABLE_MODES` contains no Ollama entry, so Hermes can never emit an Ollama-native v1 request or a `/api/generate` refusal. This is a Gateway-side obligation for direct clients, correctly out of Hermes's scope — but the Hermes plan still lists it as a Hermes acceptance test and has none. Either strike the item from the Hermes plan or record it as Gateway-owned.
6. **Hermes Task 10 integration — NOT STARTED.** `docs/verification/2026-08-15-explicit-tool-contract-integration.md` does not exist and no coordinated integration fixture was added. The plan's acceptance items 1–20 are covered by unit and composition tests only. Per the prompt, I did not create it.
7. **Gateway release gate — NOT MET.** Both designs require Gateway v1 to ship and be probed *before* Hermes transport mappings deploy (Gateway §15.1–15.3; Hermes §15.1–15.4), and require user-facing one-shot controls to become available only *after* the coordinated path passes integration tests (Hermes §15.4, plan step 5). This branch ships the CLI, TUI, and gateway `/tool-choice` controls **now**, ahead of that gate. That is a sequencing decision for the release owner, not a code defect — but it should be an explicit decision, not an accident.

---

*Nothing in this review was accepted on the authority of the prompt, the design, the plan, a commit message, a comment, or a test name. Every PASS is backed by production source I read and, where a wrong outcome was reachable, by a probe I ran and deleted.*
