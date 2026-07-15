#!/usr/bin/env python3
"""gateway-toolcall-parity — black-box tool-calling conformance test for otto-gateway.

Drives real tool round-trips through a running gateway across its three API
surfaces (Anthropic / OpenAI / Ollama) and asserts the gateway returns
STRUCTURED tool calls — not prose, not `[tool: …]` narration. On failure it
pulls the gateway's ACP capture frames and classifies the failure so the report
is actionable (Track 3a missing / Track 3b coercion bug / surfacing gap).

Stdlib only (urllib + json). No third-party dependencies.

Config (env):
  GW_URL       gateway base URL          (default http://127.0.0.1:18080)
  JS_GW_URL    legacy JS gateway URL     (optional; enables reference diffing)
  GW_TIMEOUT   per-request timeout secs  (default 120)
  TOOL_RESULT  tool-result content sent back in phase 2 (default "18°C, sunny")

Exit code: 0 if every surface PASSES, 1 otherwise (so it can gate CI/releases).

The gateway must be started SEPARATELY with a real kiro-cli and, for the
capture-based diagnosis, ACP_CAPTURE=true. This script only exercises it over
HTTP; it never modifies the gateway.
"""
from __future__ import annotations

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

ANTHROPIC_VERSION = "2023-06-01"
CITY = "Paris"
PROMPT = "What is the weather in Paris? Use the get_weather tool."

# Weather-ish keywords a coherent final answer is expected to mention.
_WEATHER_HINTS = ("weather", "sunny", "18", "°c", "degree", "temperature", "paris")

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0"}


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
_USER_MSG = {"role": "user", "content": PROMPT}


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
# Surface registry
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


# --------------------------------------------------------------------------- #
# ACP capture — fetch & classify (diagnosis on FAIL)
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


def _frame_text(frame) -> str:
    """Best-effort extraction of message text from an ACP frame's params."""
    params = frame.get("params")
    blob = json.dumps(params, ensure_ascii=False) if params is not None else ""
    return blob


def _session_update_kind(frame) -> str:
    params = frame.get("params")
    if isinstance(params, dict):
        upd = params.get("update")
        if isinstance(upd, dict):
            return str(upd.get("sessionUpdate") or "")
    return ""


def classify_capture(frames, observed_text: str):
    """Classify a tool-calling failure from capture frames + the client-visible text.

    Returns (code, human_message, first_offending_frame|None).
    """
    if not frames:
        return "no-frames", "no ACP frames captured for this request", None

    has_permission = any(
        "request_permission" in str(f.get("method") or "") for f in frames
    )
    native_toolcall_frames = [
        f for f in frames if _session_update_kind(f) in ("tool_call", "tool_call_chunk", "tool_call_update")
    ]
    prose_frames = [
        f for f in frames if _session_update_kind(f) == "agent_message_chunk"
    ]
    toolcall_json_frames = [
        f for f in prose_frames if '"tool_call"' in _frame_text(f) or '{"tool_call' in _frame_text(f)
    ]
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


# --------------------------------------------------------------------------- #
# Per-surface run (full round-trip + diagnosis on fail)
# --------------------------------------------------------------------------- #
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
    result["phase1"] = toolcall_ok(tc)

    if not result["phase1"]:
        # Diagnose from capture frames + whatever text the client did receive.
        observed = _client_observed_text(spec, resp)
        enabled, frames, note = fetch_capture(gw_url, cursor)
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

    # Phase 2 — send the tool result back, expect a coherent final answer.
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
# Reference diff (optional JS_GW_URL)
# --------------------------------------------------------------------------- #
def reference_toolcall(spec, js_gw_url: str) -> dict:
    """Run only phase-1 against the reference JS gateway; return outcome."""
    path, headers, body = spec["build1"]()
    status, resp, raw = post(js_gw_url + path, body, headers)
    if status == 0:
        return {"ok": None, "note": f"reference unreachable: {raw}"}
    tc = spec["extract"](resp)
    return {"ok": toolcall_ok(tc), "note": "", "tool_call": tc}


# --------------------------------------------------------------------------- #
# Runner + reporting
# --------------------------------------------------------------------------- #
def run_all(gw_url: str = GW_URL, js_gw_url: str = JS_GW_URL) -> tuple[list[dict], int]:
    results = []
    for spec in SURFACES:
        r = run_surface(spec, gw_url)
        if js_gw_url:
            ref = reference_toolcall(spec, js_gw_url)
            r["reference"] = ref
            # Parity is against the reference: the Go gateway must match what the
            # JS gateway does. If the reference calls the tool and we don't, that's
            # the parity gap; if the reference also fails, the expectation is off.
            if ref.get("ok") is True and not r["phase1"]:
                r["parity"] = "GAP (reference calls tool, gateway does not)"
            elif ref.get("ok") is False and r["phase1"]:
                r["parity"] = "AHEAD (gateway calls tool, reference does not)"
            elif ref.get("ok") is None:
                r["parity"] = "n/a (reference unreachable)"
            else:
                r["parity"] = "MATCH"
        results.append(r)
    exit_code = 0 if all(r["phase1"] and r["phase2"] for r in results) else 1
    return results, exit_code


def print_report(results, gw_url, js_gw_url):
    print("=" * 72)
    print("  gateway-toolcall-parity")
    print(f"  gateway:   {gw_url}")
    if js_gw_url:
        print(f"  reference: {js_gw_url}")
    print("=" * 72)
    print()
    print(f"  {'SURFACE':<34} {'TOOL-CALL':<11} {'ROUND-TRIP':<11} RESULT")
    print(f"  {'-'*34} {'-'*11} {'-'*11} ------")
    for r in results:
        p1 = "PASS" if r["phase1"] else "FAIL"
        p2 = "PASS" if r["phase2"] else ("FAIL" if r["phase1"] else "—")
        overall = "PASS" if (r["phase1"] and r["phase2"]) else "FAIL"
        line = f"  {r['label']:<34} {p1:<11} {p2:<11} {overall}"
        if "parity" in r:
            line += f"   [ref: {r['parity']}]"
        print(line)
    print()

    # Details for anything that isn't a clean pass.
    for r in results:
        if r["phase1"] and r["phase2"]:
            continue
        print("-" * 72)
        print(f"  {r['label']}  —  FAIL")
        if r.get("http") is not None:
            print(f"    http status (phase 1): {r['http']}")
        if r.get("tool_call"):
            print(f"    tool_call returned:    {json.dumps(r['tool_call'], ensure_ascii=False)}")
        if r.get("error"):
            print(f"    error:                 {r['error']}")
        diag = r.get("diagnosis")
        if diag:
            print(f"    diagnosis [{diag['code']}]: {diag['message']}")
            if diag.get("capture_note"):
                print(f"    capture:               {diag['capture_note']}")
            if diag.get("first_frame"):
                print(f"    first offending frame: {diag['first_frame']}")
            if diag.get("observed_text"):
                print(f"    client received (text):{diag['observed_text']!r}")
        if r.get("final_text") and not r["phase2"]:
            print(f"    phase-2 final text:    {r['final_text'][:200]!r}")
    print("-" * 72)
    n_pass = sum(1 for r in results if r["phase1"] and r["phase2"])
    print(f"  {n_pass}/{len(results)} surfaces at parity")
    print("=" * 72)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    try:
        _require_loopback(GW_URL)
        if JS_GW_URL:
            _require_loopback(JS_GW_URL)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    results, code = run_all(GW_URL, JS_GW_URL)
    print_report(results, GW_URL, JS_GW_URL)
    return code


if __name__ == "__main__":
    sys.exit(main())
