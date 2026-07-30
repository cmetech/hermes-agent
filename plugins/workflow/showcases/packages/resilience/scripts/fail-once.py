from __future__ import annotations

import json
import os
from pathlib import Path


node_id = os.environ["HERMES_WORKFLOW_NODE_ID"]
mode = os.environ.get("ARGUMENTS", "retry").strip() or "retry"
if node_id == "select-mode":
    print(json.dumps({"mode": mode}))
elif node_id == "retry":
    marker = Path(os.environ["ARTIFACTS_DIR"]) / ".showcase-failed-once"
    if not marker.exists():
        marker.write_text("owned\n", encoding="utf-8")
        raise SystemExit(17)
    print(json.dumps({"mode": mode, "recovered": True}))
else:
    raise SystemExit("unexpected node")
