#!/usr/bin/env python3
"""Self-test for run_parity.py — verifies the harness logic against in-process
mock gateways, with no real gateway/kiro-cli required.

It stands up two loopback mock gateways shaped like the REAL otto-gateway wire
protocol (health superset, per-surface error envelopes, SSE/NDJSON streaming,
count_tokens 404, ACP capture whose `params` is a JSON STRING):
  * "good"  — passes every check across both suites (toolcall + conformance).
  * "prose" — refuses tool calls in prose (Track 3a) with capture frames proving it.

Then it asserts the harness:
  * `run_selected("all","all", good)` → every non-SKIP check PASS, exit 0, count_tokens SKIP,
  * `run_selected("toolcall","all", good)` reproduces today's all-pass behavior,
  * surface filtering narrows the check set,
  * `run_selected("toolcall","all", prose)` → exit 1, toolcall checks FAIL, anthropic diag = track-3a,
  * plus direct unit-checks of the extractors, stream parsers, and the string-params classifier.

Run:  python3 selftest.py   (prints SELFTEST OK / exits non-zero on mismatch)
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import run_parity as rp

FINAL = "The weather in Paris is 18°C and sunny."
HELLO_REPLY = "Hello there, nice to meet you!"


def _is_phase2(body: dict) -> bool:
    """Detect the tool-result turn across all three surfaces."""
    for m in body.get("messages", []):
        if not isinstance(m, dict):
            continue
        if m.get("role") == "tool":
            return True
        content = m.get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    return True
    return False


def _tool_names(body: dict):
    names = []
    for t in body.get("tools") or []:
        if not isinstance(t, dict):
            continue
        names.append(t.get("name") or (t.get("function") or {}).get("name"))
    return names


# --- streaming payload builders (match parse_sse / parse_ndjson expectations) ---
def _anthropic_sse():
    frames = [
        ("message_start", {"type": "message_start", "message": {"id": "msg_1", "role": "assistant", "content": []}}),
        ("content_block_start", {"type": "content_block_start", "index": 0}),
        ("content_block_delta", {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {ev}\ndata: {json.dumps(d)}\n\n" for ev, d in frames)


def _openai_sse():
    chunks = [
        {"id": "chatcmpl-1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
        {"id": "chatcmpl-1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": "Hi"}, "finish_reason": None}]},
        {"id": "chatcmpl-1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _ollama_ndjson():
    lines = [
        {"model": "auto", "message": {"role": "assistant", "content": "Hi"}, "done": False},
        {"model": "auto", "message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop"},
    ]
    return "".join(json.dumps(l) + "\n" for l in lines)


def _make_handler(mode: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send_json(self, obj, code=200):
            self._send(json.dumps(obj).encode(), code, "application/json")

        def _send(self, data: bytes, code=200, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        # ------------------------------------------------------------------ GET
        def do_GET(self):
            if self.path == "/health":
                return self._send_json({
                    "status": "ok", "version": "test", "uptime_seconds": 1.0,
                    "pool": {"size": 1, "alive": 1, "busy": 0, "status": "ok"},
                    "sessions": {"active": 0}, "embeddings": {"models_loaded": 0},
                })
            if self.path == "/v1/models":
                return self._send_json({"object": "list", "data": [
                    {"id": "auto", "object": "model", "created": 0, "owned_by": "kiro"},
                    {"id": "kiro-x", "object": "model", "created": 0, "owned_by": "kiro"},
                ]})
            if self.path == "/api/tags":
                return self._send_json({"models": [
                    {"name": "auto", "model": "auto", "size": 0, "digest": ""},
                    {"name": "kiro-x", "model": "kiro-x", "size": 0, "digest": ""},
                ]})
            if self.path == "/admin/api/acp-capture":
                if mode == "prose":
                    # NOTE: params is a JSON STRING, exactly like real OTTO.
                    params = json.dumps({"update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "I'm sorry, I can't use external tools."},
                    }})
                    return self._send_json({"enabled": True, "frames": [
                        {"seq": 1, "ts": "2026-01-01T00:00:00Z", "method": "session/update",
                         "params": params, "bytes": len(params)},
                    ]})
                return self._send_json({"enabled": True, "frames": []})
            return self._send_json({"error": "not found"}, 404)

        # ----------------------------------------------------------------- POST
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            path = self.path

            if path == "/v1/messages/count_tokens":  # out of scope on OTTO
                return self._send(b"404 page not found\n", 404, "text/plain")

            if mode == "prose":
                return self._send_json(self._prose(path))

            msgs = body.get("messages")
            stream = bool(body.get("stream"))
            tools = _tool_names(body)

            # ---- validation (deterministic error envelopes, per surface) ----
            if path == "/v1/messages":
                if isinstance(msgs, list) and len(msgs) == 0:
                    return self._send_json({"type": "error", "error": {
                        "type": "invalid_request_error",
                        "message": "`messages` is required and must be a non-empty array"}}, 400)
                if "max_tokens" not in body:
                    return self._send_json({"type": "error", "error": {
                        "type": "invalid_request_error",
                        "message": "`max_tokens` is required and must be > 0"}}, 400)
            elif path == "/v1/chat/completions":
                if isinstance(msgs, list) and len(msgs) == 0:
                    return self._send_json({"error": {
                        "message": "messages must be non-empty", "type": "invalid_request_error",
                        "param": None, "code": None}}, 400)
            elif path == "/api/chat":
                if isinstance(msgs, list) and len(msgs) == 0:
                    return self._send_json({"error": "`messages` is required and must be a non-empty array"}, 400)

            # ---- tool round-trips + robustness edges ----
            if tools:
                if "write_file" in tools:
                    return self._send_json(self._write_toolcall())
                if _is_phase2(body):
                    return self._send_json(self._final(path))
                return self._send_json(self._toolcall(path))

            # ---- no tools: streaming or basic chat ----
            if stream:
                if path == "/v1/messages":
                    return self._send(_anthropic_sse().encode(), 200, "text/event-stream")
                if path == "/v1/chat/completions":
                    return self._send(_openai_sse().encode(), 200, "text/event-stream")
                if path == "/api/chat":
                    return self._send(_ollama_ndjson().encode(), 200, "application/x-ndjson")
            return self._send_json(self._basic(path))

        # --- good-mode response builders --------------------------------------
        def _toolcall(self, path):
            if path == "/v1/messages":
                return {"id": "msg_1", "type": "message", "role": "assistant", "model": "auto",
                        "stop_reason": "tool_use", "content": [
                            {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "Paris"}}]}
            if path == "/v1/chat/completions":
                return {"id": "c1", "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None, "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {
                            "name": "get_weather", "arguments": json.dumps({"city": "Paris"})}}]}}]}
            if path == "/api/chat":
                return {"model": "auto", "done": True, "message": {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "get_weather", "arguments": {"city": "Paris"}}}]}}
            return {"error": "not found"}

        def _write_toolcall(self):
            return {"id": "msg_w", "type": "message", "role": "assistant", "model": "auto",
                    "stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "toolu_w", "name": "write_file",
                    "input": {"path": "README.md", "content": "```python\nprint(\"hi\")\n```"}}]}

        def _final(self, path):
            if path == "/v1/messages":
                return {"id": "msg_2", "type": "message", "role": "assistant", "model": "auto",
                        "stop_reason": "end_turn", "content": [{"type": "text", "text": FINAL}]}
            if path == "/v1/chat/completions":
                return {"id": "c2", "choices": [{"index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant", "content": FINAL}}]}
            if path == "/api/chat":
                return {"model": "auto", "done": True, "message": {"role": "assistant", "content": FINAL}}
            return {"error": "not found"}

        def _basic(self, path):
            if path == "/v1/messages":
                return {"id": "msg_b", "type": "message", "role": "assistant", "model": "auto",
                        "stop_reason": "end_turn", "content": [{"type": "text", "text": HELLO_REPLY}]}
            if path == "/v1/chat/completions":
                return {"id": "cb", "choices": [{"index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant", "content": HELLO_REPLY}}]}
            if path == "/api/chat":
                return {"model": "auto", "done": True, "message": {"role": "assistant", "content": HELLO_REPLY}}
            return {"error": "not found"}

        # --- prose-mode (Track 3a failure) ------------------------------------
        def _prose(self, path):
            txt = "I'm sorry, I can't call external tools, but Paris is usually mild."
            if path == "/v1/messages":
                return {"id": "msg_p", "type": "message", "role": "assistant", "model": "auto",
                        "stop_reason": "end_turn", "content": [{"type": "text", "text": txt}]}
            if path == "/v1/chat/completions":
                return {"id": "cp", "choices": [{"index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant", "content": txt}}]}
            if path == "/api/chat":
                return {"model": "auto", "done": True, "message": {"role": "assistant", "content": txt}}
            return {"error": "not found"}

    return Handler


def _serve(mode: str):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(mode))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok: {msg}")


def main() -> int:
    good_srv, good_url = _serve("good")
    prose_srv, prose_url = _serve("prose")
    failures = 0
    try:
        # 1) good gateway, all suites → every non-skip check passes, exit 0.
        results, code = rp.run_selected("all", "all", good_url, "", retries=1)
        try:
            _check(code == 0, "good/all → exit 0")
            for r in results:
                if r["kind"] == "skip":
                    _check(r["status"] == "SKIP", f"good/{r['name']} → SKIP")
                    _check(r["ok"], f"good/{r['name']} → skip row asserted 404 OK")
                else:
                    _check(r["ok"], f"good/{r['name']} → PASS")
            names = {r["name"] for r in results}
            for expect in ("health", "models:openai", "tags:ollama", "stream-order:anthropic",
                           "stream-order:openai", "stream-order:ollama", "validation-empty:openai",
                           "validation-empty:ollama", "toolcall-nested-fence:anthropic",
                           "toolcall-invented-name:anthropic", "count-tokens:anthropic"):
                _check(expect in names, f"registry includes {expect}")
        except AssertionError as e:
            print(f"  FAIL: {e}"); failures += 1

        # 2) toolcall suite alone reproduces all-pass behavior.
        results, code = rp.run_selected("toolcall", "all", good_url, "", retries=1)
        try:
            _check(code == 0, "good/toolcall → exit 0")
            _check(all(r["ok"] for r in results), "good/toolcall → all checks pass")
            _check({r["suite"] for r in results} == {"toolcall"}, "toolcall filter → only toolcall checks")
        except AssertionError as e:
            print(f"  FAIL: {e}"); failures += 1

        # 3) surface filter narrows the set (anthropic + n/a only).
        results, _ = rp.run_selected("conformance", "anthropic", good_url, "", retries=1)
        try:
            surfaces = {r["surface"] for r in results}
            _check(surfaces <= {"anthropic", "n/a"}, f"surface=anthropic → {surfaces}")
            _check(all(r["ok"] for r in results if r["kind"] != "skip"), "conformance/anthropic all pass")
        except AssertionError as e:
            print(f"  FAIL: {e}"); failures += 1

        # 4) prose gateway → toolcall checks fail, exit 1, anthropic diag track-3a.
        results, code = rp.run_selected("toolcall", "all", prose_url, "", retries=1)
        try:
            _check(code == 1, "prose/toolcall → exit 1")
            _check(all(not r["ok"] for r in results), "prose/toolcall → all fail")
            a = next(r for r in results if r["name"] == "toolcall:anthropic")
            _check("track-3a" in a["detail"], f"prose/toolcall:anthropic → track-3a diagnosis (from string-params frame)")
        except AssertionError as e:
            print(f"  FAIL: {e}"); failures += 1

        # 5) reference diff: prose gateway vs good reference → toolcall:anthropic GAP.
        results, _ = rp.run_selected("toolcall", "all", prose_url, good_url, retries=1)
        try:
            a = next(r for r in results if r["name"] == "toolcall:anthropic")
            _check(a.get("parity", "").startswith("GAP"), f"prose-vs-good → GAP (got {a.get('parity')})")
        except AssertionError as e:
            print(f"  FAIL: {e}"); failures += 1

        # 6) unit-checks: extractors, stream parsers, string-params classifier.
        try:
            _check(rp.toolcall_ok({"name": "get_weather", "args": {"city": "paris"}}), "toolcall_ok: case-insensitive")
            _check(not rp.toolcall_ok({"name": "get_weather", "args": {"city": "Berlin"}}), "toolcall_ok: wrong city rejected")
            _check(rp.final_answer_ok(FINAL), "final_answer_ok: weather answer accepted")
            evs = rp.parse_sse("event: message_start\ndata: {}\n\nevent: ping\ndata: {}\n\n")
            _check([e for e, _ in evs] == ["message_start", "ping"], "parse_sse: event names")
            lines = rp.parse_ndjson('{"done":false}\n{"done":true}\n')
            _check(lines[-1]["done"] is True and len(lines) == 2, "parse_ndjson: two lines")
            # classifier must handle params-as-STRING (real OTTO shape)
            frame = {"seq": 1, "method": "session/update",
                     "params": json.dumps({"update": {"sessionUpdate": "agent_message_chunk",
                                                       "content": {"text": "no tools for me"}}})}
            code_, _, _ = rp.classify_capture([frame], "no tools for me")
            _check(code_ == "track-3a", f"classify_capture: string-params → track-3a (got {code_})")
            native = {"seq": 2, "method": "session/update",
                      "params": json.dumps({"update": {"sessionUpdate": "tool_call", "kind": "get_weather"}})}
            code2, _, _ = rp.classify_capture([native], "[tool: get_weather]")
            _check(code2 == "surfacing-gap", f"classify_capture: native tool_call → surfacing-gap (got {code2})")
        except AssertionError as e:
            print(f"  FAIL: {e}"); failures += 1
    finally:
        good_srv.shutdown()
        prose_srv.shutdown()

    print()
    if failures:
        print(f"SELFTEST FAILED ({failures} group(s))")
        return 1
    print("SELFTEST OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
