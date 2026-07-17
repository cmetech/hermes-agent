from __future__ import annotations

import json
import os
from pathlib import Path


run_dir = Path(os.environ["HERMES_WORKFLOW_RUN_DIR"])
node_id = os.environ["HERMES_WORKFLOW_NODE_ID"]
artifacts = Path(os.environ["ARTIFACTS_DIR"])
artifacts.mkdir(parents=True, exist_ok=True)


def outputs() -> list[dict]:
    findings = []
    for path in sorted((run_dir / "nodes").glob("analyze-*/*/output.json")):
        findings.append(json.loads(path.read_text(encoding="utf-8")))
    return findings


if node_id == "render-report":
    findings = outputs()
    severity = "high" if any(item["severity"] == "high" for item in findings) else "medium"
    report = {
        "schema_version": 1,
        "simulated": True,
        "disclaimer": "Sanitized fictional evidence; this is not an inventory of your laptop.",
        "severity": severity,
        "ai_requested": False,
        "findings": findings,
        "finding_count": len(findings),
    }
    (artifacts / "diagnostic-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# Fictional laptop diagnostic",
        "",
        "This report uses sanitized simulated evidence and does not describe your laptop.",
        "",
        *[f"- {item['branch']}: {item['summary']}" for item in findings],
    ]
    (artifacts / "diagnostic-report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(json.dumps({"severity": severity, "ai_requested": False, "finding_count": len(findings)}))
elif node_id in {"high-severity", "normal-severity"}:
    print(json.dumps({"branch": node_id, "simulated": True}))
elif node_id == "finalize-plan":
    feedback = ""
    approval_nodes = run_dir / "nodes" / "review-plan"
    rework = sorted(approval_nodes.glob("*/rework-output.txt"))
    if rework:
        feedback = rework[-1].read_text(encoding="utf-8").strip()
    text = (
        "# Proposed fictional remediation plan\n\n"
        "No remediation was executed. Review disk usage, startup items, and memory pressure manually.\n"
    )
    if feedback:
        text += f"\nReviewer feedback incorporated: {feedback}\n"
    (artifacts / "remediation-plan.md").write_text(text, encoding="utf-8")
    print(json.dumps({"finalized": True, "simulated": True, "feedback_incorporated": bool(feedback)}))
else:
    raise SystemExit(f"unexpected node: {node_id}")
