---
name: gateway-toolcall-parity
description: "Verify a running OTTO gateway's tool-calling at parity with the legacy JS gateway. Black-box conformance test that drives real tool round-trips through the gateway's Anthropic/OpenAI/Ollama surfaces and asserts STRUCTURED tool calls (not prose/`[tool: …]` narration). Disabled by default — enable it to run the parity check."
version: 1.0.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [gateway, tool-call, parity, conformance, kiro, kiro-cli, acp, testing, diagnostics, track-3a, track-3b]
    related_skills: []
---

# Gateway tool-call parity

## What this is

A black-box conformance test for the **OTTO gateway** (the Go service that fronts
`kiro-cli` via ACP). kiro-cli doesn't natively honor caller-supplied tools, so the
gateway must **coerce** it into emitting tool calls. This skill drives real tool
round-trips through the gateway's three API surfaces and asserts it returns
**structured** tool calls — not prose, not `[tool: …]` narration — at parity with
the reference JS gateway.

It runs the bundled `run_parity.py` harness (stdlib-only Python) and reports a
PASS/FAIL matrix; on failure it pulls the gateway's ACP capture frames and
classifies the cause (Track 3a missing / Track 3b coercion bug / surfacing gap).

**This skill only exercises the gateway over HTTP — it never modifies it.**

## How to run it

The harness lives next to this file: `run_parity.py` in this skill's directory
(`<hermes-home>/skills/gateway-toolcall-parity/run_parity.py`).

1. Confirm the gateway is running with a real `kiro-cli` on PATH, ideally started
   with `ACP_CAPTURE=true` (so failures can be diagnosed from ACP frames).
2. Run the harness with your terminal/code-execution tool, from this skill's
   directory:

   ```bash
   python run_parity.py                       # default gateway http://127.0.0.1:18080
   GW_URL=http://127.0.0.1:18080 python run_parity.py
   ```

   Config (env): `GW_URL` (gateway base URL, **without** `/v1`; default
   `http://127.0.0.1:18080` — the OTTO provider's default), `JS_GW_URL` (optional
   legacy JS gateway for reference diffing), `GW_TIMEOUT` (default 120s),
   `TOOL_RESULT` (default `18°C, sunny`).

3. Report the PASS/FAIL matrix back to the user. On any failure, include the
   printed diagnosis (code + first offending ACP frame). The harness exits
   non-zero if any surface fails, so it can gate a release check.

Validate the harness itself without a gateway: `python selftest.py` (uses
in-process mock gateways; prints `SELFTEST OK`).

## What PASS means (per surface)

For each surface the harness declares a `get_weather` tool + a prompt that should
trigger it, asserts a **structured** `get_weather(city=Paris)` call, then sends the
tool result back and asserts a coherent final answer:

| Surface | Endpoint | Structured tool call |
|---|---|---|
| Anthropic | `POST /v1/messages` | `content[]` `type:"tool_use"`, `name:"get_weather"`, `input.city=="Paris"` |
| OpenAI | `POST /v1/chat/completions` | `choices[0].message.tool_calls[0].function` name+`city` |
| Ollama | `POST /api/chat` (`stream:false`) | `message.tool_calls[0].function` name+`city` |

A prose answer or `[tool: …]` narration is a **FAIL** even if it "answers" the
question — the point is structured tool calls.

## Failure diagnosis (from `/admin/api/acp-capture`)

| ACP frames show… | Diagnosis | Meaning |
|---|---|---|
| prose refusal, no `{"tool_call"}` JSON, no `session/request_permission` denial | `track-3a` | gateway isn't applying the elicitation apparatus (no permission-deny / no strict prompt) |
| kiro emits `{"tool_call":…}` JSON in prose, client got prose | `track-3b` | gateway saw the tool_call but didn't surface it as structured `tool_calls` |
| native ACP `tool_call`/`tool_call_chunk` rendered as `[tool: …]` | `surfacing-gap` | structured-surfacing gap on OpenAI/Ollama |

## Notes

- **Localhost-only** — the harness refuses non-loopback hosts. Run it on the same
  machine as the gateway.
- This skill is **disabled by default**. Enable it in Settings → Skills (or
  `config.yaml` `skills.disabled`) when you want to run a parity check; a manual
  enable is respected and won't be re-disabled on the next launch.
- Do not add a dependency heavier than an HTTP client + JSON to the harness.
