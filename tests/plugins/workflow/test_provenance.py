from __future__ import annotations

import json

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.provenance import TriggerProvenance
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


def _request(package, prepared, *, provenance=None, source="cli"):
    return RunAdmissionRequest(
        workflow_name=package.definition.name,
        definition_digest=prepared.definition_digest,
        policy_digest=prepared.policy_digest,
        input_manifest_digest=prepared.input_manifest_digest,
        trigger_source=source,
        idempotency_key=f"intent-{source}",
        concurrency_key=package.definition.name,
        provenance=provenance,
    )


@pytest.mark.parametrize(
    ("source", "assurance"),
    [
        ("desktop", "verified_adapter"),
        ("chat", "verified_adapter"),
        ("background_agent", "verified_adapter"),
        ("cron", "system_schedule"),
        ("cli", "local_admin_claim"),
        ("api", "verified_adapter"),
    ],
)
def test_all_trigger_sources_are_durable_server_truth(
    tmp_path, workflow_writer, source, assurance
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="origins"))
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    provenance = TriggerProvenance(
        source=source,
        assurance=assurance,
        source_instance=f"{source}-instance",
        actor_id=f"{source}-actor",
        intent_key=f"intent-{source}",
        return_route=("gateway:reply" if assurance == "verified_adapter" else None),
    )

    admitted = store.start_run(
        _request(package, prepared, provenance=provenance, source=source),
        immutable_snapshot=prepared,
    )

    projection = store.get_run_status(admitted.run_id)
    assert projection["provenance"]["source"] == source
    assert projection["provenance"]["assurance"] == assurance
    assert projection["provenance"]["intent_key_digest"]
    assert "intent_key" not in projection["provenance"]
    assert projection["provenance"]["admitted_at"] == projection["created_at"]
    persisted = json.loads((store.run_directory(admitted.run_id) / "run.json").read_text())
    assert persisted["provenance"] == projection["provenance"]


def test_shell_spawned_chat_claim_cannot_assert_verified_identity() -> None:
    with pytest.raises(ValueError, match="verified_adapter"):
        TriggerProvenance.local_admin_claim(
            source="chat",
            intent_key="message-1",
            actor_id="authenticated-user",
            return_route="telegram:chat-1",
        )


def test_legacy_request_is_labeled_unknown_instead_of_assumed_cli(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="legacy"))
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)

    admitted = store.start_run(
        _request(package, prepared, source="cli"), immutable_snapshot=prepared
    )

    provenance = store.get_run_status(admitted.run_id)["provenance"]
    assert provenance["source"] == "cli"
    assert provenance["assurance"] == "legacy_unknown"
    assert provenance["source_instance"] is None


def test_provenance_must_match_legacy_trigger_source(tmp_path, workflow_writer) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="mismatch"))
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    provenance = TriggerProvenance.local_admin_claim(
        source="chat", intent_key="message-1"
    )

    with pytest.raises(ValueError, match="trigger_source"):
        store.start_run(
            _request(package, prepared, provenance=provenance, source="cli"),
            immutable_snapshot=prepared,
        )
