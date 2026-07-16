#!/usr/bin/env python3
"""gateway-toolcall-parity — gateway conformance test for otto-gateway.

Two suites, one harness, one scorecard, one exit-code gate:

  * toolcall    — black-box tool-calling parity: drive a structured tool
                  round-trip through the gateway's Anthropic/OpenAI/Ollama
                  surfaces and assert STRUCTURED tool calls (not prose / `[tool:
                  …]`), plus tool-call robustness edges (nested-fence Write,
                  invented-name remap). ACP-capture diagnosis on failure.
  * conformance — v1-messages-style functional coverage that is NOT tool-call
                  specific: health, model listing, basic non-stream chat,
                  streaming frame order (all 3 surfaces), model normalize/auto,
                  validation errors (per-surface envelopes), count_tokens (SKIP,
                  out of scope on OTTO).

Every assertion is grounded in the real OTTO gateway wire shapes (verified
against otto-gateway source), NOT the legacy JS suite's assertions.

Stdlib only (urllib + json + argparse). No third-party dependencies.

Config (env; CLI flags override):
  GW_URL      gateway base URL          (default http://127.0.0.1:18080)
  JS_GW_URL   legacy JS gateway URL     (optional; enables reference diffing)
  GW_TIMEOUT  per-request timeout secs  (default 120)
  GW_SUITE    toolcall | conformance | all   (default toolcall)
  GW_SURFACE  anthropic | openai | ollama | all  (default all)
  GW_RETRIES  attempts for flaky (model-dependent) checks (default 2)
  TOOL_RESULT tool-result content sent back in phase 2 (default "18°C, sunny")

CLI:
  --suite {toolcall,conformance,all}   --surface {anthropic,openai,ollama,all}
  --list                               list checks with their suite/surface tags
  --retries N                          override GW_RETRIES

Exit code: 0 iff every SELECTED (non-SKIP) check passes, 1 otherwise — so it can
gate CI / a release check.

The gateway must be started SEPARATELY with a real kiro-cli and, for the
capture-based diagnosis, ACP_CAPTURE=true. This script only exercises it over
HTTP; it never modifies the gateway.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
GW_URL = os.environ.get("GW_URL", "http://127.0.0.1:18080").rstrip("/")
JS_GW_URL = os.environ.get("JS_GW_URL", "").rstrip("/")
TIMEOUT = float(os.environ.get("GW_TIMEOUT", "120"))
TOOL_RESULT = os.environ.get("TOOL_RESULT", "18°C, sunny")
DEFAULT_SUITE = os.environ.get("GW_SUITE", "toolcall")
DEFAULT_SURFACE = os.environ.get("GW_SURFACE", "all")
DEFAULT_RETRIES = int(os.environ.get("GW_RETRIES", "2"))

ANTHROPIC_VERSION = "2023-06-01"
CITY = "Paris"
PROMPT = "What is the weather in Paris? Use the get_weather tool."
HELLO = "Say hello in one short sentence."

# Weather-ish keywords a coherent tool-round-trip answer is expected to mention.
_WEATHER_HINTS = ("weather", "sunny", "18", "°c", "degree", "temperature", "paris")

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0"}
SURFACE_KEYS = ("anthropic", "openai", "ollama")


# --------------------------------------------------------------------------- #
# HTTP helpers (localhost-only)
# --------------------------------------------------------------------------- #
def _require_loopback(url: str) -> None:
    host = (urlsplit(url).hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        raise ValueError(
            f"refusing non-loopback host {host!r} in {url!r}; "
            "this harness is localhost-only"
        )


def post(url: str, body: dict, headers: dict | None = None):
    """POST JSON. Returns (status, parsed_json_or_None, raw_text)."""
    _require_loopback(url)
    data = json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, _try_json(raw), raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return e.code, _try_json(raw), raw
    except (urllib.error.URLError, OSError) as e:
        return 0, None, f"<connection error: {e}>"


def post_raw(url: str, body: dict, headers: dict | None = None):
    """POST JSON, return (status, raw_text) WITHOUT JSON-parsing — for streams."""
    _require_loopback(url)
    data = json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json", "Accept": "*/*"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        return 0, f"<connection error: {e}>"


def get(url: str):
    """GET JSON. Returns (status, parsed_json_or_None, raw_text)."""
    _require_loopback(url)
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, _try_json(raw), raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return e.code, _try_json(raw), raw
    except (urllib.error.URLError, OSError) as e:
        return 0, None, f"<connection error: {e}>"


def _try_json(raw: str):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def parse_sse(raw: str):
    """Parse an SSE stream into [(event_name_or_None, data_str), ...]."""
    events = []
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        ev, datas = None, []
        for line in block.splitlines():
            if line.startswith("event:"):
                ev = line[len("event:"):].strip()
            elif line.startswith("data:"):
                datas.append(line[len("data:"):].strip())
        events.append((ev, "\n".join(datas)))
    return events


def parse_ndjson(raw: str):
    """Parse an NDJSON stream into a list of decoded objects."""
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = _try_json(line)
        if obj is not None:
            out.append(obj)
    return out


# --------------------------------------------------------------------------- #
# Tool definitions per surface
# --------------------------------------------------------------------------- #
_ANTHROPIC_TOOL = {
    "name": "get_weather",
    "description": "Get weather for a city",
    "input_schema": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
}
_FUNCTION_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}
_WRITE_TOOL_ANTHROPIC = {
    "name": "write_file",
    "description": "Write text content to a file",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    },
}
_USER_MSG = {"role": "user", "content": PROMPT}
_HELLO_MSG = {"role": "user", "content": HELLO}


# --------------------------------------------------------------------------- #
# Surface: Anthropic  POST /v1/messages
# --------------------------------------------------------------------------- #
def anthropic_build1():
    body = {
        "model": "auto",
        "max_tokens": 256,
        "messages": [_USER_MSG],
        "tools": [_ANTHROPIC_TOOL],
    }
    return "/v1/messages", {"anthropic-version": ANTHROPIC_VERSION}, body


def anthropic_extract(resp):
    """-> {name, args:dict, id} for the first tool_use block, else None."""
    if not isinstance(resp, dict):
        return None
    for block in resp.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return {
                "name": block.get("name"),
                "args": block.get("input") if isinstance(block.get("input"), dict) else {},
                "id": block.get("id"),
            }
    return None


def anthropic_build2(resp, tool_result):
    tid = (anthropic_extract(resp) or {}).get("id") or "toolu_stub"
    body = {
        "model": "auto",
        "max_tokens": 256,
        "messages": [
            _USER_MSG,
            {"role": "assistant", "content": resp.get("content", [])},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tid, "content": tool_result}
                ],
            },
        ],
        "tools": [_ANTHROPIC_TOOL],
    }
    return "/v1/messages", {"anthropic-version": ANTHROPIC_VERSION}, body


def anthropic_final(resp2):
    if not isinstance(resp2, dict):
        return ""
    parts = [
        b.get("text", "")
        for b in resp2.get("content", []) or []
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    return " ".join(p for p in parts if p).strip()


# --------------------------------------------------------------------------- #
# Surface: OpenAI  POST /v1/chat/completions
# --------------------------------------------------------------------------- #
def openai_build1():
    body = {"model": "auto", "messages": [_USER_MSG], "tools": [_FUNCTION_TOOL]}
    return "/v1/chat/completions", {}, body


def _openai_message(resp):
    if not isinstance(resp, dict):
        return None
    choices = resp.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    return msg if isinstance(msg, dict) else None


def openai_extract(resp):
    msg = _openai_message(resp)
    if not msg:
        return None
    calls = msg.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        return None
    fn = calls[0].get("function") if isinstance(calls[0], dict) else None
    if not isinstance(fn, dict):
        return None
    args = fn.get("arguments")
    if isinstance(args, str):
        args = _try_json(args) or {}
    if not isinstance(args, dict):
        args = {}
    return {"name": fn.get("name"), "args": args, "id": calls[0].get("id")}


def openai_build2(resp, tool_result):
    msg = _openai_message(resp) or {}
    tc = openai_extract(resp) or {}
    tid = tc.get("id") or "call_stub"
    body = {
        "model": "auto",
        "messages": [
            _USER_MSG,
            msg,
            {"role": "tool", "tool_call_id": tid, "content": tool_result},
        ],
        "tools": [_FUNCTION_TOOL],
    }
    return "/v1/chat/completions", {}, body


def openai_final(resp2):
    msg = _openai_message(resp2)
    if not msg:
        return ""
    content = msg.get("content")
    return (content or "").strip() if isinstance(content, str) else ""


# --------------------------------------------------------------------------- #
# Surface: Ollama  POST /api/chat
# --------------------------------------------------------------------------- #
def ollama_build1():
    body = {
        "model": "auto",
        "stream": False,
        "messages": [_USER_MSG],
        "tools": [_FUNCTION_TOOL],
    }
    return "/api/chat", {}, body


def ollama_extract(resp):
    if not isinstance(resp, dict):
        return None
    msg = resp.get("message")
    if not isinstance(msg, dict):
        return None
    calls = msg.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        return None
    fn = calls[0].get("function") if isinstance(calls[0], dict) else None
    if not isinstance(fn, dict):
        return None
    args = fn.get("arguments")
    if isinstance(args, str):
        args = _try_json(args) or {}
    if not isinstance(args, dict):
        args = {}
    return {"name": fn.get("name"), "args": args, "id": None}


def ollama_build2(resp, tool_result):
    msg = resp.get("message") if isinstance(resp, dict) else {}
    body = {
        "model": "auto",
        "stream": False,
        "messages": [
            _USER_MSG,
            msg,
            {"role": "tool", "content": tool_result},
        ],
        "tools": [_FUNCTION_TOOL],
    }
    return "/api/chat", {}, body


def ollama_final(resp2):
    if not isinstance(resp2, dict):
        return ""
    msg = resp2.get("message")
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    return (content or "").strip() if isinstance(content, str) else ""


# --------------------------------------------------------------------------- #
# Surface registry (for the toolcall round-trip)
# --------------------------------------------------------------------------- #
SURFACES = [
    {
        "key": "anthropic",
        "label": "Anthropic  /v1/messages",
        "build1": anthropic_build1,
        "extract": anthropic_extract,
        "build2": anthropic_build2,
        "final": anthropic_final,
    },
    {
        "key": "openai",
        "label": "OpenAI     /v1/chat/completions",
        "build1": openai_build1,
        "extract": openai_extract,
        "build2": openai_build2,
        "final": openai_final,
    },
    {
        "key": "ollama",
        "label": "Ollama     /api/chat",
        "build1": ollama_build1,
        "extract": ollama_extract,
        "build2": ollama_build2,
        "final": ollama_final,
    },
]
SURFACE_BY_KEY = {s["key"]: s for s in SURFACES}


# --------------------------------------------------------------------------- #
# Parity assertions
# --------------------------------------------------------------------------- #
def toolcall_ok(tc) -> bool:
    """True iff tc is a structured get_weather call with city == Paris."""
    if not tc:
        return False
    if (tc.get("name") or "").strip().lower() != "get_weather":
        return False
    args = tc.get("args") or {}
    city = args.get("city")
    if not isinstance(city, str):
        return False
    return city.strip().lower() == CITY.lower()


def final_answer_ok(text: str) -> bool:
    """A coherent final answer: non-empty and mentions the weather."""
    if not text or len(text.strip()) < 2:
        return False
    low = text.lower()
    return any(h in low for h in _WEATHER_HINTS)


_LEAK_MARKER = "[tool:"


def _bracket_tool_leak(text: str) -> bool:
    """True iff assistant text carries a `[tool: …]` tool-call NARRATION leak —
    kiro's tool call rendered as prose instead of structured tool_calls."""
    return _LEAK_MARKER in (text or "").lower()


_LEAK_DETAIL = (
    "surfacing-gap: kiro tool call leaked as '[tool: …]' narration instead of "
    "structured tool_calls"
)


# --------------------------------------------------------------------------- #
# ACP capture — fetch & classify (diagnosis on FAIL)
#
# OTTO's /admin/api/acp-capture returns frames whose `params` is a JSON STRING
# (raw params, UTF-8-truncated), not an object — see otto-gateway
# internal/admin/capture.go + internal/capture/ring.go. The classifier below
# parses that string. The frame `method` is the raw JSON-RPC method
# ("session/update" for both agent_message_chunk AND tool_call frames;
# "session/request_permission" for a permission request); the message-vs-tool
# discriminator lives inside params as (update.)sessionUpdate — see
# internal/acp/translate.go.
# --------------------------------------------------------------------------- #
def capture_cursor(gw_url: str) -> int:
    """Max frame seq currently in the capture buffer (scopes later diagnosis)."""
    status, obj, _ = get(gw_url + "/admin/api/acp-capture")
    if status == 200 and isinstance(obj, dict):
        frames = obj.get("frames") or []
        seqs = [f.get("seq", -1) for f in frames if isinstance(f, dict)]
        return max(seqs) if seqs else -1
    return -1


def fetch_capture(gw_url: str, since_seq: int):
    """Return (enabled, frames_after_cursor, note)."""
    status, obj, raw = get(gw_url + "/admin/api/acp-capture")
    if status == 0:
        return None, [], "capture endpoint unreachable"
    if status != 200 or not isinstance(obj, dict):
        return None, [], f"capture endpoint returned HTTP {status}"
    enabled = bool(obj.get("enabled"))
    frames = [f for f in (obj.get("frames") or []) if isinstance(f, dict)]
    scoped = [f for f in frames if f.get("seq", -1) > since_seq] or frames
    if not enabled:
        return False, scoped, "ACP_CAPTURE not enabled — restart gateway with ACP_CAPTURE=true"
    return True, scoped, ""


def _params_obj(frame):
    """Frame params as a dict — OTTO sends a JSON string; a mock may send a dict."""
    p = frame.get("params")
    if isinstance(p, str):
        return _try_json(p) or {}
    if isinstance(p, dict):
        return p
    return {}


def _frame_text(frame) -> str:
    """Raw params text of a frame (for substring probes like `{"tool_call`)."""
    p = frame.get("params")
    if isinstance(p, str):
        return p
    return json.dumps(p, ensure_ascii=False) if p is not None else ""


def _frame_message_text(frame) -> str:
    """The UNESCAPED agent_message_chunk text inside a frame's params, else "".

    Parsing the params (a JSON string on real OTTO) is what makes tool_call
    detection work — probing the raw params string fails because its inner quotes
    are escaped (`\\"tool_call\\"`), so a `"tool_call"` substring never matches.
    """
    p = _params_obj(frame)
    upd = p.get("update") if isinstance(p, dict) else None
    content = None
    if isinstance(upd, dict):
        content = upd.get("content")
    elif isinstance(p, dict):
        content = p.get("content")
    if isinstance(content, dict):
        t = content.get("text")
        return t if isinstance(t, str) else ""
    return content if isinstance(content, str) else ""


def _session_update_kind(frame) -> str:
    """The ACP sessionUpdate subtype inside params (wrapped or flat), else ''."""
    p = _params_obj(frame)
    upd = p.get("update") if isinstance(p, dict) else None
    if isinstance(upd, dict) and upd.get("sessionUpdate"):
        return str(upd.get("sessionUpdate"))
    if isinstance(p, dict) and p.get("sessionUpdate"):
        return str(p.get("sessionUpdate"))
    return ""


def classify_capture(frames, observed_text: str):
    """Classify a tool-calling failure from capture frames + client-visible text.

    Returns (code, human_message, first_offending_frame|None).
    """
    if not frames:
        return "no-frames", "no ACP frames captured for this request", None

    has_permission = any(
        "request_permission" in str(f.get("method") or "") for f in frames
    )
    native_toolcall_frames = [
        f for f in frames
        if _session_update_kind(f) in ("tool_call", "tool_call_chunk", "tool_call_update")
    ]
    prose_frames = [
        f for f in frames if _session_update_kind(f) == "agent_message_chunk"
    ]
    # kiro streams the `{"tool_call":…}` JSON split across many tiny chunks, so
    # detect it on the CONCATENATED unescaped message text, not per-frame.
    full_prose = "".join(_frame_message_text(f) for f in prose_frames)
    has_toolcall_json = '"tool_call"' in full_prose or '{"tool_call' in full_prose
    toolcall_json_frames = [
        f for f in prose_frames if "tool_call" in _frame_message_text(f)
    ] or (prose_frames[:1] if has_toolcall_json else [])
    client_shows_bracket_tool = "[tool:" in (observed_text or "").lower()

    # 1) Native ACP tool_call surfaced as narration → structured-surfacing gap.
    if native_toolcall_frames:
        note = (
            "structured-surfacing gap: native ACP tool_call/tool_call_chunk was "
            "rendered as narration"
        )
        if client_shows_bracket_tool:
            note += " ([tool: …] seen in client response)"
        return "surfacing-gap", note, native_toolcall_frames[0]

    # 2) kiro emitted {"tool_call":…} JSON in prose but client got narration/prose.
    if toolcall_json_frames:
        return (
            "track-3b",
            "Track 3b coercion bug: gateway saw {\"tool_call\":…} JSON in "
            "agent_message_chunk but did not surface it as structured tool_calls",
            toolcall_json_frames[0],
        )

    # 3) Prose only, no permission denial, no tool_call JSON → apparatus not applied.
    if prose_frames and not has_permission and not toolcall_json_frames:
        return (
            "track-3a",
            "Track 3a missing: elicitation apparatus not applied — prose refusal "
            "with no session/request_permission denial and no {\"tool_call\"} JSON "
            "(gateway isn't using the strict function-caller prompt / isn't denying "
            "kiro's built-in tools)",
            prose_frames[0],
        )

    # 4) Permission request present → kiro tried its own built-in tools.
    if has_permission:
        first = next(
            (f for f in frames if "request_permission" in str(f.get("method") or "")),
            None,
        )
        return (
            "permission",
            "kiro emitted session/request_permission — gateway may be allowing "
            "kiro's built-in tools instead of denying them (denyKiroTools)",
            first,
        )

    return "inconclusive", "no structured tool call and frames are inconclusive", frames[0]


def format_frame(frame) -> str:
    if not isinstance(frame, dict):
        return "(none)"
    method = frame.get("method", "?")
    seq = frame.get("seq", "?")
    params = _frame_text(frame)
    if len(params) > 200:
        params = params[:200] + "…"
    return f"seq={seq} method={method} params={params}"


def _client_observed_text(spec, resp) -> str:
    """Whatever human-readable text the gateway returned in phase 1 (for diagnosis)."""
    key = spec["key"]
    if key == "anthropic":
        return anthropic_final(resp)
    if key == "openai":
        return openai_final(resp)
    if key == "ollama":
        return ollama_final(resp)
    return ""


# --------------------------------------------------------------------------- #
# toolcall suite — per-surface 2-phase round-trip (with ACP diagnosis on fail)
# --------------------------------------------------------------------------- #
def _looks_like_ciphertext(s) -> bool:
    """Heuristic: a long base64/base64url-ish token with mixed letters+digits —
    the shape of an AES-GCM PII ciphertext, not a plain value like 'Paris'."""
    if not isinstance(s, str) or len(s) < 24:
        return False
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_+/=")
    if any(ch not in allowed for ch in s):
        return False
    return any(c.isdigit() for c in s) and any(c.isalpha() for c in s)


def _diagnose_bad_toolcall(tc):
    """A structured tool_call WAS surfaced but isn't get_weather(Paris).

    Distinguishes the real failure modes so the report is accurate:
      * wrong-tool-name — surfaced a call for a different tool
      * pii-redaction   — right tool + id, but the arg looks like ENCRYPTED PII
                          (the round-trip decrypt isn't covering tool-call args)
      * args-fidelity   — right tool + id, but the arg value is otherwise wrong
    """
    name = (tc.get("name") or "").strip().lower()
    city = (tc.get("args") or {}).get("city")
    tid = tc.get("id")
    if name != "get_weather":
        return "wrong-tool-name", (
            f"structured tool_call surfaced but name={tc.get('name')!r} "
            "(expected get_weather) — surfaced a call for the wrong tool")
    if isinstance(city, str) and _looks_like_ciphertext(city):
        return "pii-redaction", (
            f"tool_call surfaced correctly (name=get_weather, id={tid!r}) but "
            f"city={city!r} looks like ENCRYPTED PII, not plaintext. 'Paris' is a "
            "LOCATION; with the gateway's PII hook in encrypt/mask mode + NER on, the "
            "argument is redacted and the round-trip decrypt is NOT restoring tool_call "
            "ARGUMENT values (only message text). => tool-calling itself works; the fix "
            "is to decrypt tool_call arguments on the response round-trip. To isolate "
            "tool-calling, re-run with the PII hook off (e.g. PII_NER_ENABLED=false).")
    return "args-fidelity", (
        f"tool_call surfaced (name=get_weather, id={tid!r}) but city={city!r} "
        "(expected 'Paris') — tool name/id correct, argument value wrong")


def run_surface(spec, gw_url: str, tool_result: str = TOOL_RESULT) -> dict:
    result = {
        "key": spec["key"],
        "label": spec["label"],
        "phase1": False,
        "phase2": False,
        "tool_call": None,
        "final_text": "",
        "error": None,
        "diagnosis": None,
        "http": None,
        "observed_text": "",
    }

    cursor = capture_cursor(gw_url)

    path, headers, body = spec["build1"]()
    status, resp, raw = post(gw_url + path, body, headers)
    result["http"] = status
    if status == 0:
        result["error"] = f"connection failed: {raw}"
        return result
    if resp is None:
        result["error"] = f"HTTP {status}, non-JSON body: {raw[:200]}"
    tc = spec["extract"](resp)
    result["tool_call"] = tc
    observed = _client_observed_text(spec, resp)
    result["observed_text"] = observed

    # A `[tool: …]` leak is a hard surfacing-gap FAIL even if a call is present.
    if _bracket_tool_leak(observed):
        enabled, frames, note = fetch_capture(gw_url, cursor)
        _, _, frame = classify_capture(frames, observed)
        result["phase1"] = False
        result["diagnosis"] = {
            "code": "surfacing-gap",
            "message": _LEAK_DETAIL,
            "capture_note": note,
            "capture_enabled": enabled,
            "first_frame": format_frame(frame) if frame else None,
            "observed_text": (observed or "")[:200],
        }
        return result

    result["phase1"] = toolcall_ok(tc)

    if not result["phase1"]:
        enabled, frames, note = fetch_capture(gw_url, cursor)
        if tc is not None:
            # A structured tool_call WAS surfaced — the surfacing apparatus works;
            # diagnose the value, don't misfire the "no tool call" classifier.
            code, human = _diagnose_bad_toolcall(tc)
            _, _, frame = classify_capture(frames, observed)
        else:
            code, human, frame = classify_capture(frames, observed)
        result["diagnosis"] = {
            "code": code,
            "message": human,
            "capture_note": note,
            "capture_enabled": enabled,
            "first_frame": format_frame(frame) if frame else None,
            "observed_text": (observed or "")[:200],
        }
        return result

    path2, headers2, body2 = spec["build2"](resp, tool_result)
    status2, resp2, raw2 = post(gw_url + path2, body2, headers2)
    if status2 == 0:
        result["error"] = f"phase-2 connection failed: {raw2}"
        return result
    final = spec["final"](resp2)
    result["final_text"] = final
    result["phase2"] = final_answer_ok(final)
    if not result["phase2"]:
        result["error"] = (
            "phase-2 produced no coherent final answer "
            f"(text={final[:120]!r}, HTTP {status2})"
        )
    return result


def _toolcall_check(surface_key):
    spec = SURFACE_BY_KEY[surface_key]

    def fn(gw_url):
        r = run_surface(spec, gw_url)
        if r["phase1"] and r["phase2"]:
            return True, f"structured get_weather({CITY}) + tool_result round-trip OK"
        parts = []
        if r.get("http") is not None:
            parts.append(f"HTTP {r['http']}")
        if r.get("tool_call"):
            parts.append(f"tool_call={json.dumps(r['tool_call'], ensure_ascii=False)}")
        if r.get("error"):
            parts.append(r["error"])
        diag = r.get("diagnosis")
        if diag:
            parts.append(f"diagnosis[{diag['code']}]: {diag['message']}")
            if diag.get("capture_note"):
                parts.append(f"capture: {diag['capture_note']}")
            if diag.get("first_frame"):
                parts.append(f"frame: {diag['first_frame']}")
        return False, " | ".join(parts) or "tool-call round-trip failed"

    return fn


# --------------------------------------------------------------------------- #
# toolcall suite — robustness edges (Anthropic-first; model-dependent)
# --------------------------------------------------------------------------- #
def check_nested_fence_anthropic(gw_url):
    body = {
        "model": "auto",
        "max_tokens": 512,
        "messages": [{
            "role": "user",
            "content": (
                "Use the write_file tool to create README.md. Its `content` "
                "argument must be EXACTLY this markdown, fences included:\n"
                "```python\nprint(\"hi\")\n```"
            ),
        }],
        "tools": [_WRITE_TOOL_ANTHROPIC],
    }
    status, resp, raw = post(gw_url + "/v1/messages", body, {"anthropic-version": ANTHROPIC_VERSION})
    if _bracket_tool_leak(anthropic_final(resp)):
        return False, _LEAK_DETAIL
    tc = anthropic_extract(resp)
    if not tc or (tc.get("name") or "").lower() != "write_file":
        return False, f"expected write_file tool_use, got {tc} (HTTP {status})"
    content = (tc.get("args") or {}).get("content", "")
    if "```" not in (content or ""):
        return False, f"nested fence lost from tool args; content={content[:120]!r}"
    return True, "write_file tool_use surfaced with nested ``` fence intact"


def check_invented_name_anthropic(gw_url):
    # Only get_weather is offered; per otto-gateway internal/engine/coerce.go
    # pickBestTool, whatever name kiro emits is remapped to the sole offered tool.
    body = {
        "model": "auto",
        "max_tokens": 256,
        "messages": [{
            "role": "user",
            "content": "Look up the weather in Paris using whatever tool is available.",
        }],
        "tools": [_ANTHROPIC_TOOL],
    }
    status, resp, raw = post(gw_url + "/v1/messages", body, {"anthropic-version": ANTHROPIC_VERSION})
    if _bracket_tool_leak(anthropic_final(resp)):
        return False, _LEAK_DETAIL
    tc = anthropic_extract(resp)
    if not tc:
        return False, f"no structured tool_call surfaced (HTTP {status})"
    if (tc.get("name") or "").lower() != "get_weather":
        return False, f"surfaced tool != sole offered tool; got {tc.get('name')!r}"
    return True, f"surfaced tool remapped to sole offered tool get_weather (city={(tc.get('args') or {}).get('city')!r})"


# --------------------------------------------------------------------------- #
# conformance suite — deterministic + model-dependent checks
# --------------------------------------------------------------------------- #
def check_health(gw_url):
    status, obj, raw = get(gw_url + "/health")
    if status != 200:
        return False, f"expected HTTP 200, got {status}"
    if not isinstance(obj, dict) or obj.get("status") != "ok":
        return False, f"health status != ok: {raw[:160]}"
    pool = obj.get("pool")
    if not isinstance(pool, dict) or not isinstance(pool.get("alive"), int):
        return False, f"health.pool.alive missing/not-int: {raw[:160]}"
    return True, f"status=ok, pool.alive={pool['alive']}, pool.size={pool.get('size')}"


def check_models_openai(gw_url):
    status, obj, raw = get(gw_url + "/v1/models")
    if status != 200 or not isinstance(obj, dict) or obj.get("object") != "list":
        return False, f"/v1/models not an object-list (HTTP {status}): {raw[:160]}"
    data = obj.get("data")
    if not isinstance(data, list) or not data:
        return False, "/v1/models data[] empty"
    ids = [d.get("id") for d in data if isinstance(d, dict)]
    if "auto" not in ids:
        return False, f"/v1/models catalog missing 'auto': {ids[:8]}"
    return True, f"/v1/models: {len(ids)} model(s) incl 'auto'"


def check_tags_ollama(gw_url):
    status, obj, raw = get(gw_url + "/api/tags")
    if status != 200 or not isinstance(obj, dict):
        return False, f"/api/tags not JSON (HTTP {status}): {raw[:160]}"
    models = obj.get("models")
    if not isinstance(models, list) or not models:
        return False, "/api/tags models[] empty"
    names = [m.get("name") or m.get("model") for m in models if isinstance(m, dict)]
    if "auto" not in names:
        return False, f"/api/tags catalog missing 'auto': {names[:8]}"
    return True, f"/api/tags: {len(names)} model(s) incl 'auto'"


def _basic_anthropic(gw_url):
    body = {"model": "auto", "max_tokens": 128, "messages": [_HELLO_MSG]}
    status, resp, raw = post(gw_url + "/v1/messages", body, {"anthropic-version": ANTHROPIC_VERSION})
    if status != 200:
        return False, f"HTTP {status}: {raw[:160]}"
    if not isinstance(resp, dict) or resp.get("type") != "message" or resp.get("role") != "assistant":
        return False, f"unexpected envelope: {raw[:160]}"
    txt = anthropic_final(resp)
    if not txt:
        return False, "200 but no text content block"
    return True, f"text answer + stop_reason={resp.get('stop_reason')!r}: {txt[:60]!r}"


def _basic_openai(gw_url):
    body = {"model": "auto", "messages": [_HELLO_MSG]}
    status, resp, raw = post(gw_url + "/v1/chat/completions", body)
    if status != 200:
        return False, f"HTTP {status}: {raw[:160]}"
    txt = openai_final(resp)
    if not txt:
        return False, f"200 but empty message.content: {raw[:160]}"
    return True, f"choices[0].message.content: {txt[:60]!r}"


def _basic_ollama(gw_url):
    body = {"model": "auto", "stream": False, "messages": [_HELLO_MSG]}
    status, resp, raw = post(gw_url + "/api/chat", body)
    if status != 200:
        return False, f"HTTP {status}: {raw[:160]}"
    txt = ollama_final(resp)
    if not txt:
        return False, f"200 but empty message.content: {raw[:160]}"
    return True, f"message.content: {txt[:60]!r}"


_BASIC = {"anthropic": _basic_anthropic, "openai": _basic_openai, "ollama": _basic_ollama}


def _stream_anthropic(gw_url):
    body = {"model": "auto", "max_tokens": 128, "stream": True, "messages": [_HELLO_MSG]}
    status, raw = post_raw(gw_url + "/v1/messages", body, {"anthropic-version": ANTHROPIC_VERSION})
    if status != 200:
        return False, f"HTTP {status}: {raw[:160]}"
    events = [ev for ev, _ in parse_sse(raw) if ev]
    non_ping = [e for e in events if e != "ping"]
    if not non_ping or non_ping[0] != "message_start":
        return False, f"first SSE event != message_start: {non_ping[:4]}"
    if "content_block_start" not in events or "message_stop" not in events:
        return False, f"missing content_block_start/message_stop: {events[:8]}"
    return True, f"SSE order OK ({'→'.join(non_ping[:6])}…)"


def _stream_openai(gw_url):
    body = {"model": "auto", "stream": True, "messages": [_HELLO_MSG]}
    status, raw = post_raw(gw_url + "/v1/chat/completions", body)
    if status != 200:
        return False, f"HTTP {status}: {raw[:160]}"
    datas = [d for _, d in parse_sse(raw)]
    if not any(d == "[DONE]" for d in datas):
        return False, "no `data: [DONE]` terminator"
    first = next((_try_json(d) for d in datas if d and d != "[DONE]"), None)
    if not isinstance(first, dict) or first.get("object") != "chat.completion.chunk":
        return False, f"first chunk not chat.completion.chunk: {str(first)[:120]}"
    return True, f"{len(datas)} SSE frames, chat.completion.chunk + [DONE]"


def _stream_ollama(gw_url):
    body = {"model": "auto", "stream": True, "messages": [_HELLO_MSG]}
    status, raw = post_raw(gw_url + "/api/chat", body)
    if status != 200:
        return False, f"HTTP {status}: {raw[:160]}"
    lines = parse_ndjson(raw)
    if not lines or lines[-1].get("done") is not True:
        return False, f"final NDJSON line not done:true ({len(lines)} lines)"
    if not any(l.get("done") is False for l in lines):
        return False, "no non-final done:false frame"
    return True, f"NDJSON stream OK ({len(lines)} lines, final done:true)"


_STREAM = {"anthropic": _stream_anthropic, "openai": _stream_openai, "ollama": _stream_ollama}


def check_model_normalize_anthropic(gw_url):
    # OTTO does NO HTTP-level model validation (verified in the gateway): an
    # arbitrary id is forwarded to kiro. So the conformance contract is "no 400";
    # 200 (kiro accepted) and 500 (kiro rejected the unknown id) are BOTH valid —
    # a 400 would mean OTTO wrongly added HTTP model validation.
    body = {"model": "totally-made-up-model-xyz", "max_tokens": 64, "messages": [_HELLO_MSG]}
    status, resp, raw = post(gw_url + "/v1/messages", body, {"anthropic-version": ANTHROPIC_VERSION})
    if status == 400:
        return False, f"arbitrary model → HTTP 400 — OTTO must not do HTTP-level model validation: {raw[:120]}"
    if status == 200:
        txt = anthropic_final(resp)
        return (bool(txt), f"arbitrary model accepted by kiro → 200 + text: {txt[:50]!r}"
                if txt else "200 but no text content")
    if status == 500:
        return True, "arbitrary model → 500 (no HTTP validation; kiro rejected the unknown id — acceptable)"
    return True, f"arbitrary model → HTTP {status} (no HTTP-level 400 validation)"


def _validation_anthropic_max_tokens(gw_url):
    body = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}  # no max_tokens
    status, resp, raw = post(gw_url + "/v1/messages", body, {"anthropic-version": ANTHROPIC_VERSION})
    if status != 400:
        return False, f"expected 400, got {status}: {raw[:120]}"
    err = resp.get("error") if isinstance(resp, dict) else None
    if not (isinstance(resp, dict) and resp.get("type") == "error"
            and isinstance(err, dict) and err.get("type") == "invalid_request_error"):
        return False, f"400 but not anthropic error envelope: {raw[:160]}"
    return True, "missing max_tokens → 400 {type:error, error.type:invalid_request_error}"


def _validation_empty_anthropic(gw_url):
    body = {"model": "auto", "max_tokens": 64, "messages": []}
    status, resp, raw = post(gw_url + "/v1/messages", body, {"anthropic-version": ANTHROPIC_VERSION})
    if status != 400:
        return False, f"expected 400, got {status}: {raw[:120]}"
    err = resp.get("error") if isinstance(resp, dict) else None
    if not (isinstance(resp, dict) and resp.get("type") == "error"
            and isinstance(err, dict) and err.get("type") == "invalid_request_error"):
        return False, f"400 but not anthropic error envelope: {raw[:160]}"
    return True, "empty messages → 400 anthropic {type:error, error.type:invalid_request_error}"


def _validation_empty_openai(gw_url):
    body = {"model": "auto", "messages": []}
    status, resp, raw = post(gw_url + "/v1/chat/completions", body)
    if status != 400:
        return False, f"expected 400, got {status}: {raw[:120]}"
    err = resp.get("error") if isinstance(resp, dict) else None
    if not (isinstance(err, dict) and err.get("type") == "invalid_request_error"):
        return False, f"400 but not openai error envelope {{error:{{type,param,code}}}}: {raw[:160]}"
    return True, "empty messages → 400 openai {error:{type:invalid_request_error, param, code}}"


def _validation_empty_ollama(gw_url):
    body = {"model": "auto", "messages": []}
    status, resp, raw = post(gw_url + "/api/chat", body)
    if status != 400:
        return False, f"expected 400, got {status}: {raw[:120]}"
    err = resp.get("error") if isinstance(resp, dict) else None
    if not isinstance(err, str) or not err:
        return False, f"400 but not ollama flat {{error:'…'}} envelope: {raw[:160]}"
    return True, "empty messages → 400 ollama {error:'…'} (flat string)"


_VALIDATION_EMPTY = {
    "anthropic": _validation_empty_anthropic,
    "openai": _validation_empty_openai,
    "ollama": _validation_empty_ollama,
}


def check_count_tokens_skip(gw_url):
    # count_tokens is OUT OF SCOPE on OTTO (anthropic/adapter.go:14-15). The route
    # doesn't exist → chi default NotFound → 404. Never a hard FAIL.
    body = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}
    status, resp, raw = post(gw_url + "/v1/messages/count_tokens", body, {"anthropic-version": ANTHROPIC_VERSION})
    if status == 404:
        return True, "count_tokens out of scope → 404 (as expected)"
    return False, f"count_tokens: expected 404 (out of scope), got HTTP {status}"


# --------------------------------------------------------------------------- #
# Check registry
# --------------------------------------------------------------------------- #
class Check:
    def __init__(self, name, suite, surface, fn, flaky=False, kind="normal", js=False):
        self.name = name          # stable id
        self.suite = suite        # 'toolcall' | 'conformance'
        self.surface = surface    # 'anthropic' | 'openai' | 'ollama' | 'n/a'
        self.fn = fn              # (gw_url) -> (pass: bool, detail: str)
        self.flaky = flaky        # model-dependent → retry
        self.kind = kind          # 'normal' | 'skip' (skip never gates exit code)
        self.js = js              # has a legacy-JS analog → reference-diffable


def build_registry():
    reg = []
    # toolcall suite — per-surface round-trip (model-dependent)
    for k in SURFACE_KEYS:
        reg.append(Check(f"toolcall:{k}", "toolcall", k, _toolcall_check(k), flaky=True, js=(k == "anthropic")))
    # toolcall robustness edges (Anthropic-first)
    reg.append(Check("toolcall-nested-fence:anthropic", "toolcall", "anthropic", check_nested_fence_anthropic, flaky=True))
    reg.append(Check("toolcall-invented-name:anthropic", "toolcall", "anthropic", check_invented_name_anthropic, flaky=True))

    # conformance suite — deterministic infrastructure
    reg.append(Check("health", "conformance", "n/a", check_health, js=True))
    reg.append(Check("models:openai", "conformance", "openai", check_models_openai, js=True))
    reg.append(Check("tags:ollama", "conformance", "ollama", check_tags_ollama, js=True))
    # basic non-stream chat (model-dependent)
    for k in SURFACE_KEYS:
        reg.append(Check(f"basic:{k}", "conformance", k, _BASIC[k], flaky=True, js=(k == "anthropic")))
    # streaming frame order (deterministic framing)
    for k in SURFACE_KEYS:
        reg.append(Check(f"stream-order:{k}", "conformance", k, _STREAM[k], js=(k == "anthropic")))
    # model normalize/auto (model-dependent, Anthropic)
    reg.append(Check("model-normalize:anthropic", "conformance", "anthropic", check_model_normalize_anthropic, flaky=True, js=True))
    # validation errors (deterministic, per-surface envelopes)
    reg.append(Check("validation-max-tokens:anthropic", "conformance", "anthropic", _validation_anthropic_max_tokens, js=True))
    for k in SURFACE_KEYS:
        reg.append(Check(f"validation-empty:{k}", "conformance", k, _VALIDATION_EMPTY[k], js=(k == "anthropic")))
    # count_tokens — SKIP row (out of scope), never a hard FAIL
    reg.append(Check("count-tokens:anthropic", "conformance", "anthropic", check_count_tokens_skip, kind="skip", js=True))
    return reg


def select_checks(registry, suite: str, surface: str):
    out = []
    for c in registry:
        if suite != "all" and c.suite != suite:
            continue
        if surface != "all" and c.surface not in (surface, "n/a"):
            continue
        out.append(c)
    return out


# --------------------------------------------------------------------------- #
# Runner + reporting
# --------------------------------------------------------------------------- #
def run_check(check: Check, gw_url: str, retries: int) -> dict:
    attempts = retries if check.flaky else 1
    ok, detail, n = False, "", 0
    for n in range(1, attempts + 1):
        try:
            ok, detail = check.fn(gw_url)
        except Exception as e:  # a check bug must not abort the whole run
            ok, detail = False, f"check raised: {e!r}"
        if ok:
            break
    if check.kind == "skip":
        status = "SKIP"
    else:
        status = "PASS" if ok else "FAIL"
    return {
        "name": check.name, "suite": check.suite, "surface": check.surface,
        "kind": check.kind, "flaky": check.flaky, "status": status,
        "detail": detail, "attempts": n, "ok": ok,
    }


def run_selected(suite: str, surface: str, gw_url: str = GW_URL,
                 js_gw_url: str = JS_GW_URL, retries: int = DEFAULT_RETRIES,
                 on_result=None):
    """Run the selected checks. If on_result is given it is called with each
    result the moment that check completes — this is what lets `main()` stream
    the matrix live (one line per check) instead of batching at the end."""
    registry = build_registry()
    checks = select_checks(registry, suite, surface)
    results = []
    for c in checks:
        r = run_check(c, gw_url, retries)
        if js_gw_url and c.js:
            try:
                ref_ok, _ = c.fn(js_gw_url)
            except Exception:
                ref_ok = None
            if ref_ok is None:
                r["parity"] = "n/a (reference error)"
            elif r["ok"] == ref_ok:
                r["parity"] = "MATCH"
            elif ref_ok and not r["ok"]:
                r["parity"] = "GAP (reference passes, gateway fails)"
            else:
                r["parity"] = "AHEAD (gateway passes, reference fails)"
        if on_result:
            on_result(r)
        results.append(r)
    # exit 0 iff every selected non-SKIP check passes
    exit_code = 0 if all(r["ok"] for r in results if r["kind"] != "skip") else 1
    return results, exit_code


# Back-compat: the original toolcall-only entry point (used by older callers).
def run_all(gw_url: str = GW_URL, js_gw_url: str = JS_GW_URL):
    return run_selected("toolcall", "all", gw_url, js_gw_url)


def print_header(gw_url, js_gw_url, suite, surface):
    print("=" * 78)
    print("  gateway-toolcall-parity")
    print(f"  gateway:   {gw_url}")
    if js_gw_url:
        print(f"  reference: {js_gw_url}")
    print(f"  suite:     {suite}    surface: {surface}")
    print("=" * 78)
    print()
    print(f"  {'CHECK':<34} {'SUITE':<11} {'SURFACE':<9} RESULT")
    print(f"  {'-'*34} {'-'*11} {'-'*9} ------", flush=True)


def format_result_line(r) -> str:
    res = r["status"]
    if r["status"] == "FAIL" and r["flaky"]:
        res = f"FAIL*({r['attempts']})"
    line = f"  {r['name']:<34} {r['suite']:<11} {r['surface']:<9} {res}"
    if "parity" in r:
        line += f"   [ref: {r['parity']}]"
    return line


def print_footer(results):
    print()
    for r in results:
        if r["ok"] and r["status"] != "SKIP":
            continue
        tag = r["status"]
        print("-" * 78)
        print(f"  {r['name']}  —  {tag}" + ("  (flaky/model-dependent)" if r["flaky"] else ""))
        print(f"    {r['detail']}")
    print("-" * 78)
    gated = [r for r in results if r["kind"] != "skip"]
    n_pass = sum(1 for r in gated if r["ok"])
    n_skip = sum(1 for r in results if r["kind"] == "skip")
    print(f"  {n_pass}/{len(gated)} checks pass"
          + (f"  ({n_skip} skipped)" if n_skip else ""))
    if any(r["flaky"] and not r["ok"] for r in gated):
        print("  * = flaky/model-dependent check failed all retries — may be a prose "
              "response, not a hard regression. Re-run or inspect the detail.")
    print("=" * 78)


def print_list(suite, surface):
    registry = build_registry()
    checks = select_checks(registry, suite, surface)
    print(f"{'CHECK':<34} {'SUITE':<12} {'SURFACE':<10} FLAGS")
    print(f"{'-'*34} {'-'*12} {'-'*10} -----")
    for c in checks:
        flags = []
        if c.flaky:
            flags.append("flaky")
        if c.kind == "skip":
            flags.append("skip")
        if c.js:
            flags.append("js-diff")
        print(f"{c.name:<34} {c.suite:<12} {c.surface:<10} {','.join(flags)}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="run_parity.py",
        description="Gateway conformance test (toolcall + conformance suites).",
    )
    p.add_argument("--suite", choices=["toolcall", "conformance", "all"], default=DEFAULT_SUITE)
    p.add_argument("--surface", choices=["anthropic", "openai", "ollama", "all"], default=DEFAULT_SURFACE)
    p.add_argument("--retries", type=int, default=DEFAULT_RETRIES,
                   help="attempts for flaky/model-dependent checks (default from GW_RETRIES)")
    p.add_argument("--list", action="store_true", help="list checks and their suite/surface tags")
    args = p.parse_args(argv)

    if args.list:
        print_list(args.suite, args.surface)
        return 0

    try:
        _require_loopback(GW_URL)
        if JS_GW_URL:
            _require_loopback(JS_GW_URL)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Stream one line per check as it completes (so a live terminal / the
    # co-worker agent can show progress), then print the failure details + summary.
    print_header(GW_URL, JS_GW_URL, args.suite, args.surface)
    results, code = run_selected(
        args.suite, args.surface, GW_URL, JS_GW_URL, args.retries,
        on_result=lambda r: print(format_result_line(r), flush=True),
    )
    print_footer(results)
    return code


def print_report(results, gw_url, js_gw_url, suite, surface):
    """Non-streaming full report (back-compat for external callers)."""
    print_header(gw_url, js_gw_url, suite, surface)
    for r in results:
        print(format_result_line(r))
    print_footer(results)


if __name__ == "__main__":
    sys.exit(main())
