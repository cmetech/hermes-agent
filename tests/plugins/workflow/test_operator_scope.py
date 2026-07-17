from plugins.workflow.admission import RunAdmissionRequest
import pytest

from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


def test_explicit_run_id_does_not_bypass_operator_scope(tmp_path, workflow_writer):
    store = RunStore(tmp_path)
    package = load_workflow(workflow_writer(tmp_path / "package", name="demo"))
    prepared = store.prepare_run_snapshot(package)
    result = store.start_run(RunAdmissionRequest(
        workflow_name="demo",
        definition_digest=prepared.definition_digest,
        policy_digest=prepared.policy_digest,
        input_manifest_digest=prepared.input_manifest_digest,
        trigger_source="chat",
        idempotency_key="message-1",
        concurrency_key="demo",
        operator_scope="profile:alice:conversation:one",
    ), immutable_snapshot=prepared)
    assert result.run_id
    assert store.get_run_status(result.run_id, operator_scope="profile:alice:conversation:one")
    with pytest.raises(KeyError):
        store.get_run_status(result.run_id, operator_scope="profile:alice:conversation:two")
