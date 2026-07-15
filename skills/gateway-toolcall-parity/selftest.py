#!/usr/bin/env python3
"""Self-test for run_parity.py — verifies the harness logic against in-process
mock gateways, with no real gateway/kiro-cli required.

It stands up two loopback mock gateways:
  * "good"  — returns structured tool calls on all 3 surfaces + a final answer.
  * "prose" — refuses in prose (Track 3a), and its ACP capture shows an
              agent_message_chunk prose refusal with no permission denial and no
              {"tool_call"} JSON.

Then it asserts the harness:
  * marks every surface PASS against "good" (exit 0),
  * marks every surface FAIL against "prose" with diagnosis code "track-3a" (exit 1),
  * reports parity "GAP" when "prose" is the gateway and "good" is the reference.

Run:  python3 selftest.py   (prints SELFTEST OK / exits non-zero on mismatch)
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import run_parity as rp

FINAL = "The weather in Paris is 18°C and sunny."


def _is_phase2(body: dict) -> bool:
    """Detect the tool-result turn across all three surfaces."""
    for m in body.get("messages", []):
        if not isinstance(m, dict):
            continue
        if m.get("role") == "tool":  # OpenAI / Ollama tool-result turn
            return True
        content = m.get("content")
        if isinstance(content, list):  # Anthropic tool_result block
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    return True
    return False


def _make_handler(mode: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def _send(self, obj, code=200):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/admin/api/acp-capture":
                if mode == "prose":
                    self._send({
                        "enabled": True,
                        "frames": [{
                            "seq": 1, "ts": 1.0, "method": "session/update",
                            "params": {"update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text",
                                            "text": "I'm sorry, I can't use external tools."},
                            }},
                            "bytes": 120,
                        }],
                    })
                else:
                    self._send({"enabled": True, "frames": []})
                return
            self._send({"error": "not found"}, 404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            phase2 = _is_phase2(body)

            if mode == "prose":
                self._send(self._prose(self.path))
                return
            self._send(self._final(self.path) if phase2 else self._toolcall(self.path))

        # --- good-mode responses --------------------------------------------
        def _toolcall(self, path):
            if path == "/v1/messages":
                return {"id": "msg_1", "type": "message", "role": "assistant",
                        "model": "auto", "stop_reason": "tool_use",
                        "content": [{"type": "tool_use", "id": "toolu_1",
                                     "name": "get_weather", "input": {"city": "Paris"}}]}
            if path == "/v1/chat/completions":
                return {"id": "c1", "choices": [{"index": 0, "finish_reason": "tool_calls",
                        "message": {"role": "assistant", "content": None, "tool_calls": [
                            {"id": "call_1", "type": "function", "function": {
                                "name": "get_weather",
                                "arguments": json.dumps({"city": "Paris"})}}]}}]}
            if path == "/api/chat":
                return {"model": "auto", "done": True, "message": {
                    "role": "assistant", "content": "", "tool_calls": [
                        {"function": {"name": "get_weather", "arguments": {"city": "Paris"}}}]}}
            return {"error": "not found"}

        def _final(self, path):
            if path == "/v1/messages":
                return {"id": "msg_2", "type": "message", "role": "assistant",
                        "model": "auto", "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": FINAL}]}
            if path == "/v1/chat/completions":
                return {"id": "c2", "choices": [{"index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant", "content": FINAL}}]}
            if path == "/api/chat":
                return {"model": "auto", "done": True,
                        "message": {"role": "assistant", "content": FINAL}}
            return {"error": "not found"}

        # --- prose-mode responses (Track 3a failure) ------------------------
        def _prose(self, path):
            txt = "I'm sorry, I can't call external tools, but Paris is usually mild."
            if path == "/v1/messages":
                return {"id": "msg_p", "type": "message", "role": "assistant",
                        "model": "auto", "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": txt}]}
            if path == "/v1/chat/completions":
                return {"id": "cp", "choices": [{"index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant", "content": txt}}]}
            if path == "/api/chat":
                return {"model": "auto", "done": True,
                        "message": {"role": "assistant", "content": txt}}
            return {"error": "not found"}

    return Handler


def _serve(mode: str):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(mode))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
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
        # 1) good gateway → everything passes, exit 0.
        results, code = rp.run_all(good_url, "")
        try:
            _check(code == 0, "good gateway → exit code 0")
            for r in results:
                _check(r["phase1"], f"good/{r['key']} → structured tool_call (phase 1)")
                _check(r["phase2"], f"good/{r['key']} → coherent final answer (phase 2)")
        except AssertionError as e:
            print(f"  FAIL: {e}"); failures += 1

        # 2) prose gateway → everything fails, diagnosis track-3a, exit 1.
        results, code = rp.run_all(prose_url, "")
        try:
            _check(code == 1, "prose gateway → exit code 1")
            for r in results:
                _check(not r["phase1"], f"prose/{r['key']} → no structured tool_call")
                diag = r.get("diagnosis") or {}
                _check(diag.get("code") == "track-3a",
                       f"prose/{r['key']} → diagnosis track-3a (got {diag.get('code')})")
                _check(bool(diag.get("first_frame")),
                       f"prose/{r['key']} → first offending frame reported")
        except AssertionError as e:
            print(f"  FAIL: {e}"); failures += 1

        # 3) reference diff: prose gateway vs good reference → parity GAP.
        results, _ = rp.run_all(prose_url, good_url)
        try:
            for r in results:
                _check(r.get("parity", "").startswith("GAP"),
                       f"prose-vs-good/{r['key']} → parity GAP (got {r.get('parity')})")
        except AssertionError as e:
            print(f"  FAIL: {e}"); failures += 1

        # 4) unit-check the tool-call extractors + assertions directly.
        try:
            _check(rp.toolcall_ok({"name": "get_weather", "args": {"city": "paris"}}),
                   "toolcall_ok: case-insensitive city match")
            _check(not rp.toolcall_ok({"name": "get_weather", "args": {"city": "Berlin"}}),
                   "toolcall_ok: wrong city rejected")
            _check(not rp.toolcall_ok(None), "toolcall_ok: None rejected")
            _check(rp.final_answer_ok(FINAL), "final_answer_ok: weather answer accepted")
            _check(not rp.final_answer_ok(""), "final_answer_ok: empty rejected")
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
