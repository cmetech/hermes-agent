#!/usr/bin/env python3
"""Deterministic ACP peer for the released-Gateway Task 10 gate.

The fixture records only bounded event names and prompt indexes. It never
records prompt text, schemas, arguments, results, credentials, or session IDs.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _write(frame: dict) -> None:
    sys.stdout.write(json.dumps(frame, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _respond(request_id, result: dict) -> None:
    if request_id is not None:
        _write({"jsonrpc": "2.0", "id": request_id, "result": result})


def _record(event: str, *, index: int | None = None) -> None:
    path = os.environ.get("OTTO_TASK10_EVENT_FILE")
    if not path:
        return
    payload = {"event": event}
    if index is not None:
        payload["index"] = index
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, separators=(",", ":")) + "\n")


def _notification(update: dict) -> None:
    _write({
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "fixture-session",
            "update": update,
        },
    })


def _emit(action: dict) -> str:
    kind = action.get("kind", "text")
    if kind == "text":
        _notification({
            "sessionUpdate": "agent_message_chunk",
            "content": {"text": action.get("text", "fixture response")},
        })
        return "end_turn"
    if kind == "tool":
        _notification({
            "sessionUpdate": "tool_call",
            "toolCallId": "fixture-call",
            "title": "fixture tool",
            "kind": action.get("name", "tool_call"),
            "rawInput": action.get("arguments", {}),
        })
        return "tool_use"
    if kind == "sleep":
        time.sleep(float(action.get("seconds", 5)))
        return "end_turn"
    if kind == "exit":
        os._exit(7)
    return "end_turn"


def main() -> int:
    sequence_path = Path(os.environ["OTTO_TASK10_SEQUENCE_FILE"])
    actions = json.loads(sequence_path.read_text(encoding="utf-8"))
    prompt_index = 0

    for raw in sys.stdin:
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            continue
        method = frame.get("method")
        request_id = frame.get("id")

        if method == "initialize":
            _respond(
                request_id,
                {
                    "protocolVersion": 1,
                    "agentCapabilities": {
                        "promptCapabilities": {
                            "image": True,
                            "audio": False,
                            "embeddedContext": True,
                        }
                    },
                },
            )
        elif method == "session/new":
            _respond(
                request_id,
                {
                    "sessionId": "fixture-session",
                    "models": {
                        "availableModels": [
                            {"modelId": "auto", "name": "Auto"},
                            {"modelId": "selected-model", "name": "Selected Model"},
                        ],
                        "currentModelId": "auto",
                    },
                },
            )
        elif method == "session/set_model":
            _respond(request_id, {})
        elif method == "session/prompt":
            prompt_index += 1
            _record("prompt", index=prompt_index)
            action = actions[min(prompt_index - 1, len(actions) - 1)]
            _respond(request_id, {"stopReason": _emit(action)})
        elif method == "session/cancel":
            _record("cancel")
            _respond(request_id, {})
        elif method == "ping":
            _respond(request_id, {})
        elif request_id is not None:
            _respond(request_id, {})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
