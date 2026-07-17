from __future__ import annotations

import json

from plugins.workflow.cli import build_catalog, show_package
from plugins.workflow.schema import load_workflow


def _catalog_package(workflow_writer, root):
    (root / "commands").mkdir(parents=True)
    (root / "commands" / "send.md").write_text(
        "---\ndescription: Send report\nargument-hint: <recipient>\n---\nSECRET_COMMAND_BODY $ARGUMENTS\n",
        encoding="utf-8",
    )
    path = workflow_writer(
        root / "workflows",
        name="cataloged",
        filename="cataloged.yaml",
        description="Safe description",
        nodes=[
            {"id": "collect", "bash": "SECRET_SHELL"},
            {
                "id": "review",
                "approval": {"message": "SECRET_APPROVAL"},
                "depends_on": ["collect"],
            },
            {
                "id": "send",
                "command": "send",
                "provider": "claude",
                "skills": ["reports"],
                "allowed_tools": ["Read"],
                "depends_on": ["review"],
            },
        ],
    )
    path.with_name("cataloged.hermes.yaml").write_text(
        "outward_action_nodes: [send]\n", encoding="utf-8"
    )
    return load_workflow(path, source="project", precedence=1)


def test_list_catalog_has_stable_summary_without_bodies(workflow_writer, tmp_path):
    package = _catalog_package(workflow_writer, tmp_path / "package")
    entries = build_catalog(
        [package],
        available_tools={"read_file"},
        provider_capabilities={"claude": set()},
    )

    assert len(entries) == 1
    entry = entries[0]
    assert set(entry) == {
        "name",
        "description",
        "source",
        "precedence",
        "compatibility",
        "runnable",
    }
    assert entry["name"] == "cataloged"
    serialized = json.dumps(entry, sort_keys=True)
    assert "SECRET_" not in serialized


def test_show_adds_redacted_operational_metadata_and_cron_join(
    workflow_writer, tmp_path
):
    package = _catalog_package(workflow_writer, tmp_path / "package")
    detail = show_package(
        package,
        available_tools={"read_file"},
        provider_capabilities={"claude": set()},
        cron_jobs=[
            {
                "id": "job-1",
                "enabled": True,
                "prompt": "hermes workflow run cataloged --arguments SECRET_CRON_BODY",
                "schedule_display": "daily at 09:00",
                "next_run_at": "2026-07-17T13:00:00Z",
                "state": "scheduled",
            }
        ],
    )

    assert detail["argument_hints"] == {"send": "<recipient>"}
    assert detail["node_type_counts"] == {"approval": 1, "bash": 1, "command": 1}
    assert detail["approval_nodes"] == ["review"]
    assert detail["outward_action_nodes"] == ["send"]
    assert detail["required_tools"] == ["read_file"]
    assert detail["required_skills"] == ["reports"]
    assert detail["required_providers"] == ["claude"]
    assert detail["topology_text"] == "collect -> review -> send"
    assert detail["topology_mermaid"].startswith("flowchart LR\n")
    assert detail["topology_warnings"] == []
    assert detail["related_cron_schedules"] == [
        {
            "id": "job-1",
            "enabled": True,
            "schedule": "daily at 09:00",
            "next_run_at": "2026-07-17T13:00:00Z",
            "state": "scheduled",
        }
    ]
    serialized = json.dumps(detail, sort_keys=True)
    for secret in (
        "SECRET_COMMAND_BODY",
        "SECRET_SHELL",
        "SECRET_APPROVAL",
        "SECRET_CRON_BODY",
    ):
        assert secret not in serialized
