from __future__ import annotations

import json
import os
from pathlib import Path


run_dir = Path(os.environ["HERMES_WORKFLOW_RUN_DIR"])
node_id = os.environ["HERMES_WORKFLOW_NODE_ID"]
evidence = json.loads((run_dir / "inputs" / "evidence").read_text(encoding="utf-8"))
branch = node_id.removeprefix("analyze-")
value = evidence[branch]
severity = "high" if branch in {"memory", "storage", "startup"} else "medium"
finding = {
    "branch": branch,
    "severity": severity,
    "simulated": True,
    "evidence": value,
    "summary": f"Fictional {branch} evidence was analyzed deterministically.",
}
print(json.dumps(finding, sort_keys=True))
