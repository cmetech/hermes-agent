---
name: gateway-toolcall-parity
description: "Conformance-test a running OTTO gateway. Two suites, one harness: `toolcall` (structured tool-call parity across Anthropic/OpenAI/Ollama — not prose/`[tool: …]`) and `conformance` (v1-messages-style: health, model listing, basic + streaming chat, model normalize/auto, validation errors, count_tokens). Triggerable by CLI flag or natural language; exits non-zero to gate a release. Disabled by default — enable it to run a check."
version: 1.1.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [gateway, tool-call, parity, conformance, regression, v1-messages, kiro, kiro-cli, acp, testing, diagnostics, streaming, validation, track-3a, track-3b]
    related_skills: []
---

# Gateway conformance (tool-call + v1-messages)

## What this is

A black-box conformance test for the **OTTO gateway** (the Go service that fronts
`kiro-cli` via ACP). It exercises the gateway over HTTP and grades it — it **never
modifies the gateway**. Two suites share one scorecard and one exit-code gate:

- **`toolcall`** — kiro-cli doesn't natively honor caller tools, so the gateway must
  **coerce** it into emitting structured tool calls. This suite drives a `get_weather`
  round-trip across all three surfaces and asserts a **structured** tool call (not
  prose, not `[tool: …]` narration), plus two robustness edges (a Write call whose
  content holds a nested ``` fence; invented-name→sole-offered-tool remap). On failure
  it pulls ACP capture frames and classifies the cause (Track 3a / 3b / surfacing gap).
- **`conformance`** — v1-messages-style functional coverage that is NOT tool-call
  specific: `/health`, model listing (`/api/tags` + `/v1/models`), basic non-stream
  chat, streaming frame order (SSE for Anthropic/OpenAI, NDJSON for Ollama), model
  normalize/auto, and validation errors (each surface's real error envelope).
  `count_tokens` is out of scope on OTTO → a `SKIP` row that confirms 404 (never a FAIL).

Every assertion is grounded in OTTO's **real** wire shapes (verified against the
gateway source), not the legacy JS suite's assertions.

## How to run it

The harness `run_parity.py` lives next to this file
(`<hermes-home>/skills/gateway-toolcall-parity/run_parity.py`). Run it with the
terminal/code-execution tool, from this skill's directory.

```bash
python run_parity.py                       # default: toolcall suite, all surfaces
python run_parity.py --suite conformance   # the v1-messages functional suite
python run_parity.py --suite all           # everything (full gate)
python run_parity.py --suite all --surface anthropic   # filter to one surface
python run_parity.py --list                # list checks with their suite/surface tags
```

- Confirm the gateway is running with a real `kiro-cli` on PATH, ideally started with
  `ACP_CAPTURE=true` (so tool-call failures can be diagnosed from ACP frames).
- Report the PASS/FAIL matrix back to the user. On failure, include the printed detail
  (and for tool-call failures, the diagnosis code + first offending ACP frame).
- **Exit code is 0 iff every selected non-SKIP check passes** — so it gates CI/releases.

Config (env; CLI flags override): `GW_URL` (gateway base URL **without** `/v1`; default
`http://127.0.0.1:18080` — the OTTO provider's default), `JS_GW_URL` (optional legacy JS
gateway for reference diffing), `GW_SUITE`/`GW_SURFACE` (defaults for the flags),
`GW_TIMEOUT` (120s), `GW_RETRIES` (flaky-check attempts, 2), `TOOL_RESULT` (`18°C, sunny`).

Validate the harness itself without a gateway: `python selftest.py` (in-process
OTTO-shaped mock gateways; prints `SELFTEST OK`).

## Reporting back (when a user triggers this in chat)

The harness always prints a full PASS/FAIL matrix to stdout and sets an exit code —
your job is to run it and relay that, never to end the turn silently.

- **Tell the user you're starting** (a real-gateway run can take a minute), THEN run it.
- **Show progress in stages** instead of one long blocking call: run
  `python run_parity.py --suite conformance` first (fast, deterministic) and post its
  matrix, then `python run_parity.py --suite toolcall` (model-dependent) and post its
  matrix, then a one-line combined summary (e.g. "N/M passed, exit 0; 1 FAIL:
  toolcall:openai → track-3a").
- **Always relay the result.** Post the PASS/FAIL matrix and exit code back to the user.
  If the tool output is long, summarize the matrix but quote any FAIL detail verbatim
  (diagnosis code + first ACP frame). Do **not** finish with an empty message after the
  tool runs.
- **If your own reply comes back empty right after the tool runs**, that is a gateway
  "empty stream after a tool result" symptom — the very class of bug this skill exists to
  catch. Note it to the user and fall back to the terminal (`python run_parity.py
  --suite all`), whose output can't be swallowed.

## Natural-language → command

Map a plain-English request to the invocation:

| The user says… | Run |
|---|---|
| "run the tool-call parity check" / "check structured tool calls" | `python run_parity.py --suite toolcall` |
| "run the full gateway conformance / v1-messages suite" / "regression-test the gateway" | `python run_parity.py --suite conformance` |
| "run everything" / "full parity gate" | `python run_parity.py --suite all` |
| "…on the Anthropic surface only" (or openai/ollama) | append `--surface anthropic` |
| "…and diff against the JS gateway" | set `JS_GW_URL=http://127.0.0.1:<port>` (reference mode) |
| "what does it check?" / "list the checks" | `python run_parity.py --list` |

## What PASS means

**toolcall** (per surface): a **structured** `get_weather(city=Paris)` call — Anthropic
`content[] type:"tool_use"`, OpenAI `choices[0].message.tool_calls[0].function`, Ollama
`message.tool_calls[0].function` — then a tool-result round-trip to a coherent final
answer. A prose answer or `[tool: …]` narration is a **FAIL**.

**conformance** (highlights): `/health` → `status:"ok"` + `pool.alive`; `/v1/models` &
`/api/tags` list a non-empty catalog including `auto`; streaming emits the right frame
order (Anthropic `message_start…message_stop`, OpenAI `chat.completion.chunk`…`[DONE]`,
Ollama NDJSON ending `done:true`); validation returns each surface's real 400 envelope
(Anthropic `{type:"error",error:{type:"invalid_request_error"}}`, OpenAI
`{error:{type,param,code}}`, Ollama `{error:"…"}`).

Model-dependent checks (tool-call, basic chat, model-normalize) are flagged **flaky**
and retried; a `FAIL*(n)` may be a one-off prose response, not a hard regression —
re-run or read the detail.

## Failure diagnosis (tool-call, from `/admin/api/acp-capture`)

| ACP frames show… | Diagnosis | Meaning |
|---|---|---|
| prose refusal, no `{"tool_call"}` JSON, no `session/request_permission` denial | `track-3a` | gateway isn't applying the elicitation apparatus |
| kiro emits `{"tool_call":…}` JSON in prose, client got prose | `track-3b` | saw the tool_call but didn't surface it as structured `tool_calls` |
| native ACP `tool_call`/`tool_call_chunk` rendered as `[tool: …]` | `surfacing-gap` | structured-surfacing gap on OpenAI/Ollama |

(OTTO capture frames carry `params` as a JSON **string**; the classifier parses it.)

## Reference (parity) mode

Set `JS_GW_URL` to run the JS-analog checks against the legacy gateway too. Each such
check reports **MATCH** / **GAP** (reference passes, OTTO fails — the parity gap) /
**AHEAD** (OTTO passes, reference fails). Checks with no JS analog skip the diff.

## Notes

- **Localhost-only** — the harness refuses non-loopback hosts; run it on the same
  machine as the gateway.
- **Disabled by default.** Enable in Settings → Skills (or `config.yaml`
  `skills.disabled`) to run a check; a manual enable is respected across launches.
- Stdlib-only (HTTP client + JSON). Do not add a heavier dependency.

## Files

- `run_parity.py` — the harness + CLI (check registry, both suites, stdlib-only).
- `selftest.py` — in-process OTTO-shaped mock gateways proving the harness (run anytime).
