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
# A ciphertext-shaped token (like an AES-GCM PII redaction of "Paris").
CIPHER_CITY = "9TvKUqBiQ-i3ujOASILCYBHPI_xW_CEYlrWe8FqA9m-D"


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

            if mode == "leak":
                return self._send_json(self._leak(path))

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
                if "run_shell" in tools:
                    return self._send_json(self._exec_toolcall(path))
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
            city = CIPHER_CITY if mode == "cipher" else "Paris"  # cipher = redacted PII
            if path == "/v1/messages":
                return {"id": "msg_1", "type": "message", "role": "assistant", "model": "auto",
                        "stop_reason": "tool_use", "content": [
                            {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": city}}]}
            if path == "/v1/chat/completions":
                return {"id": "c1", "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None, "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {
                            "name": "get_weather", "arguments": json.dumps({"city": city})}}]}}]}
            if path == "/api/chat":
                return {"model": "auto", "done": True, "message": {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "get_weather", "arguments": {"city": city}}}]}}
            return {"error": "not found"}

        def _exec_toolcall(self, path):
            args = {"command": "echo hi"}
            if path == "/v1/messages":
                return {"id": "msg_e", "type": "message", "role": "assistant", "model": "auto",
                        "stop_reason": "tool_use", "content": [
                            {"type": "tool_use", "id": "toolu_e", "name": "run_shell", "input": args}]}
            if path == "/v1/chat/completions":
                return {"id": "ce", "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None, "tool_calls": [
                        {"id": "call_e", "type": "function", "function": {
                            "name": "run_shell", "arguments": json.dumps(args)}}]}}]}
            if path == "/api/chat":
                return {"model": "auto", "done": True, "message": {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "run_shell", "arguments": args}}]}}
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
            text = ("I am the Kiro CLI; running Hermes skills requires the Hermes agent."
                    if mode == "identity_bleed" else HELLO_REPLY)
            if path == "/v1/messages":
                return {"id": "msg_b", "type": "message", "role": "assistant", "model": "auto",
                        "stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}
            if path == "/v1/chat/completions":
                return {"id": "cb", "choices": [{"index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant", "content": text}}]}
            if path == "/api/chat":
                return {"model": "auto", "done": True, "message": {"role": "assistant", "content": text}}
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

        # --- leak-mode (surfacing-gap: [tool: …] narration, no structured call) ---
        def _leak(self, path):
            txt = "[tool: execute] I'll run that for you."
            if path == "/v1/messages":
                return {"id": "msg_l", "type": "message", "role": "assistant", "model": "auto",
                        "stop_reason": "end_turn", "content": [{"type": "text", "text": txt}]}
            if path == "/v1/chat/completions":
                return {"id": "cl", "choices": [{"index": 0, "finish_reason": "stop",
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
    cipher_srv, cipher_url = _serve("cipher")
    leak_srv, leak_url = _serve("leak")
    identity_srv, identity_url = _serve("identity_bleed")
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
            for k in ("anthropic", "openai", "ollama"):
                r = next(x for x in results if x["name"] == f"toolcall:{k}")
                _check(r["ok"], f"good/toolcall:{k} PASS")
                e = next(x for x in results if x["name"] == f"toolcall-exec:{k}")
                _check(e["ok"], f"good/toolcall-exec:{k} PASS (got: {e['detail'][:60]})")
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
            # track-3b: {"tool_call"} JSON split across streamed chunks, params as
            # JSON STRING with ESCAPED quotes — the exact real-OTTO shape that the
            # old raw-substring probe missed and mislabeled track-3a.
            tb = [
                {"seq": 3, "method": "session/update", "params": json.dumps(
                    {"update": {"sessionUpdate": "agent_message_chunk", "content": {"text": "```json\n{\"tool"}}})},
                {"seq": 4, "method": "session/update", "params": json.dumps(
                    {"update": {"sessionUpdate": "agent_message_chunk", "content": {"text": "_call\": {\"name\": \"get_weather\"}}"}}})},
            ]
            code3, _, _ = rp.classify_capture(tb, "")
            _check(code3 == "track-3b", f"classify_capture: escaped split-chunk tool_call → track-3b (got {code3})")
            # PII-aware diagnosis of a surfaced-but-bad tool_call
            _check(rp._looks_like_ciphertext(CIPHER_CITY), "_looks_like_ciphertext: token detected")
            _check(not rp._looks_like_ciphertext("Paris"), "_looks_like_ciphertext: plain value rejected")
            cp, _ = rp._diagnose_bad_toolcall({"name": "get_weather", "args": {"city": CIPHER_CITY}, "id": "call_1"})
            _check(cp == "pii-redaction", f"_diagnose_bad_toolcall: ciphertext → pii-redaction (got {cp})")
            cf, _ = rp._diagnose_bad_toolcall({"name": "get_weather", "args": {"city": "Berlin"}, "id": "x"})
            _check(cf == "args-fidelity", f"_diagnose_bad_toolcall: wrong city → args-fidelity (got {cf})")
            cw, _ = rp._diagnose_bad_toolcall({"name": "other_tool", "args": {}, "id": "x"})
            _check(cw == "wrong-tool-name", f"_diagnose_bad_toolcall: wrong tool → wrong-tool-name (got {cw})")
        except AssertionError as e:
            print(f"  FAIL: {e}"); failures += 1

        # 7) cipher gateway (surfaces get_weather with a ciphertext arg) → each
        #    toolcall check FAILs with the pii-redaction diagnosis, not track-3a.
        results, code = rp.run_selected("toolcall", "all", cipher_url, "", retries=1)
        try:
            _check(code == 1, "cipher/toolcall → exit 1")
            for k in ("anthropic", "openai", "ollama"):
                r = next(x for x in results if x["name"] == f"toolcall:{k}")
                _check("pii-redaction" in r["detail"],
                       f"cipher/toolcall:{k} → pii-redaction diagnosis (got: {r['detail'][:60]})")
        except AssertionError as e:
            print(f"  FAIL: {e}"); failures += 1

        # 8) leak gateway ([tool: …] narration, no structured call) → every
        #    toolcall check hard-FAILs with the surfacing-gap diagnosis, exit 1.
        results, code = rp.run_selected("toolcall", "all", leak_url, "", retries=1)
        try:
            _check(code == 1, "leak/toolcall → exit 1")
            for k in ("anthropic", "openai", "ollama"):
                r = next(x for x in results if x["name"] == f"toolcall:{k}")
                _check("surfacing-gap" in r["detail"],
                       f"leak/toolcall:{k} → surfacing-gap (got: {r['detail'][:70]})")
                e = next(x for x in results if x["name"] == f"toolcall-exec:{k}")
                _check("surfacing-gap" in e["detail"],
                       f"leak/toolcall-exec:{k} → surfacing-gap (got: {e['detail'][:70]})")
            for name in ("toolcall-nested-fence:anthropic", "toolcall-invented-name:anthropic"):
                r = next(x for x in results if x["name"] == name)
                _check("surfacing-gap" in r["detail"],
                       f"leak/{name} → surfacing-gap (got: {r['detail'][:70]})")
        except AssertionError as e:
            print(f"  FAIL: {e}"); failures += 1

        # 9) identity — good gateway PASSES; a "Kiro CLI / requires the Hermes
        #    agent" reply FAILS identity:openai.
        results, _ = rp.run_selected("conformance", "openai", good_url, "", retries=1)
        try:
            r = next(x for x in results if x["name"] == "identity:openai")
            _check(r["ok"], f"good/identity:openai PASS (got: {r['detail'][:60]})")
        except (AssertionError, StopIteration) as e:
            print(f"  FAIL: {e}"); failures += 1

        results, code = rp.run_selected("conformance", "openai", identity_url, "", retries=1)
        try:
            r = next(x for x in results if x["name"] == "identity:openai")
            _check(not r["ok"], "bleed/identity:openai FAIL")
            _check("persona bleed" in r["detail"],
                   f"bleed/identity:openai → persona bleed (got: {r['detail'][:70]})")
        except (AssertionError, StopIteration) as e:
            print(f"  FAIL: {e}"); failures += 1
    finally:
        good_srv.shutdown()
        prose_srv.shutdown()
        cipher_srv.shutdown()
        leak_srv.shutdown()
        identity_srv.shutdown()
        leak_srv.shutdown()

    print()
    if failures:
        print(f"SELFTEST FAILED ({failures} group(s))")
        return 1
    print("SELFTEST OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
