from __future__ import annotations

import hashlib
import json

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.provenance import (
    TriggerProvenance,
    legacy_projection_provenance,
)
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


def _request(
    package,
    prepared,
    *,
    provenance=None,
    source="cli",
    namespace="profile-local:cli",
    concurrency_policy="queue",
):
    return RunAdmissionRequest(
        workflow_name=package.definition.name,
        definition_digest=prepared.definition_digest,
        policy_digest=prepared.policy_digest,
        input_manifest_digest=prepared.input_manifest_digest,
        trigger_source=source,
        idempotency_key=f"intent-{source}",
        idempotency_namespace=namespace,
        concurrency_key=package.definition.name,
        concurrency_policy=concurrency_policy,
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


def test_verified_principals_have_separate_idempotency_namespaces(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="principals"))
    store = RunStore(tmp_path / "home")

    admitted = []
    for principal in ("alice", "bob"):
        prepared = store.prepare_run_snapshot(package)
        provenance = TriggerProvenance(
            source="api",
            assurance="verified_adapter",
            intent_key="intent-api",
            actor_id=principal,
        )
        admitted.append(
            store.start_run(
                _request(
                    package,
                    prepared,
                    provenance=provenance,
                    source="api",
                    namespace=f"verified:test:{principal}",
                    concurrency_policy="allow",
                ),
                immutable_snapshot=prepared,
            )
        )

    assert [result.disposition for result in admitted] == ["created", "created"]
    assert admitted[0].run_id != admitted[1].run_id


def test_return_route_and_source_instance_rotation_do_not_change_start_identity(
    tmp_path, workflow_writer
) -> None:
    package = load_workflow(workflow_writer(tmp_path / "package", name="delivery"))
    store = RunStore(tmp_path / "home")

    def admit(source_instance: str, actor_id: str, return_route: str):
        prepared = store.prepare_run_snapshot(package)
        provenance = TriggerProvenance(
            source="api",
            assurance="verified_adapter",
            intent_key="intent-api",
            source_instance=source_instance,
            actor_id=actor_id,
            return_route=return_route,
        )
        request = _request(
            package,
            prepared,
            provenance=provenance,
            source="api",
            namespace="verified:test:alice",
        )
        return store.start_run(request, immutable_snapshot=prepared), request

    (first, first_request) = admit(
        "api-instance-a", "display-actor-a", "route-capability-a"
    )
    (retry, _retry_request) = admit(
        "api-instance-b", "display-actor-b", "route-capability-b"
    )

    assert retry.disposition == "existing"
    assert retry.run_id == first.run_id
    projection = store.load_run(first.run_id)
    assert projection["provenance"]["return_route"] == (
        "route-capability-a"
    )
    assert projection["provenance"]["actor_id"] == "display-actor-a"
    assert store._start_digest(first_request) == store._start_digest_from_projection(
        projection
    )


def test_semantic_provenance_excludes_volatile_audit_and_delivery_fields() -> None:
    provenance = TriggerProvenance(
        source="api",
        assurance="verified_adapter",
        intent_key="intent",
        source_instance="pid-like-instance",
        actor_id="verified-principal",
        return_route="opaque-route",
    )

    assert provenance.semantic_record(
        idempotency_namespace="verified:test:principal"
    ) == {
        "source": "api",
        "assurance": "verified_adapter",
        "idempotency_namespace_digest": hashlib.sha256(
            b"verified:test:principal"
        ).hexdigest(),
    }


@pytest.mark.parametrize("source", ["desktop", "api"])
def test_authenticated_api_preserves_server_derived_source(source) -> None:
    provenance = TriggerProvenance.authenticated_api(
        source=source,
        assurance="verified_adapter",
        intent_key="intent",
        source_instance="verified-instance",
        principal="verified-principal",
    )

    assert provenance.source == source


def test_authenticated_api_accepts_desktop_local_admin_claim() -> None:
    provenance = TriggerProvenance.authenticated_api(
        source="desktop",
        assurance="local_admin_claim",
        intent_key="intent",
        source_instance="api:local-admin",
        principal="profile-local-dashboard",
    )

    assert provenance.source == "desktop"
    assert provenance.assurance == "local_admin_claim"
    assert provenance.claimed_actor == "profile-local-dashboard"


@pytest.mark.parametrize("source", ["chat", "background_agent"])
def test_authenticated_api_rejects_non_rest_source_vocabulary(source) -> None:
    with pytest.raises(
        ValueError,
        match="authenticated API source must be api or desktop",
    ):
        TriggerProvenance.authenticated_api(
            source=source,
            assurance="verified_adapter",
            intent_key="intent",
            source_instance="verified-instance",
            principal="verified-principal",
        )


@pytest.mark.parametrize(
    ("source", "assurance", "namespace", "expected"),
    [
        (
            "cli",
            "local_admin_claim",
            "profile-local:cli",
            "c92576387ecd9ecbcacf78f6ec3d941a7a475d208493a6578ca3670cd0017242",
        ),
        (
            "chat",
            "verified_adapter",
            "gateway:test:user",
            "e9e30271519cdd3d618c2f1f2d9669b460d3b98758136c5abde67e31da630b6f",
        ),
        (
            "cron",
            "system_schedule",
            "profile-local:cron",
            "1fb21de7f341635623d64d3e40a0e68ffaefecdb4a52c368e7df6753d7bebdb7",
        ),
        (
            "api",
            "verified_adapter",
            "api:service:test:writer",
            "70a542d1f8dd40dc360756ed5c9a68dc9a197643f6e75eed15e829ed5dc2b3e8",
        ),
        (
            "desktop",
            "local_admin_claim",
            "api:profile-local-dashboard",
            "e83faa3f8e0a03c54110fd8a660c4609240a4e5242807512df88515b680aa4c6",
        ),
    ],
)
def test_existing_start_digest_fixtures_are_byte_stable(
    source, assurance, namespace, expected
) -> None:
    if source in {"api", "desktop"}:
        provenance = TriggerProvenance.authenticated_api(
            source=source,
            assurance=assurance,
            intent_key=f"intent-{source}",
            source_instance=f"{source}-instance",
            principal=f"{source}-principal",
        )
    else:
        provenance = TriggerProvenance(
            source=source,
            assurance=assurance,
            intent_key=f"intent-{source}",
            source_instance=f"{source}-instance",
        )
    request = RunAdmissionRequest(
        workflow_name="digest-fixture",
        definition_digest="1" * 64,
        policy_digest="2" * 64,
        input_manifest_digest="3" * 64,
        trigger_source=source,
        idempotency_key=f"intent-{source}",
        idempotency_namespace=namespace,
        concurrency_key="digest-fixture",
        run_metadata={"zeta": "last", "alpha": "one"},
        provenance=provenance,
    )

    assert RunStore._start_digest(request) == expected


def test_existing_api_start_digest_fixture_is_byte_stable() -> None:
    request = RunAdmissionRequest(
        workflow_name="digest-fixture",
        definition_digest="1" * 64,
        policy_digest="2" * 64,
        input_manifest_digest="3" * 64,
        trigger_source="api",
        idempotency_key="intent-api",
        idempotency_namespace="api:service:test:writer",
        concurrency_key="digest-fixture",
        operator_scope="service:test:writer",
        run_metadata={"zeta": "last", "alpha": "one"},
        provenance=TriggerProvenance.authenticated_api(
            assurance="verified_adapter",
            intent_key="intent-api",
            source_instance="api:token:test",
            principal="service:test:writer",
        ),
    )

    assert RunStore._start_digest(request) == (
        "2432809a726b15ac48c7a0ccc7c2c7ed122fe79c8d68f0c85ecc862f8d91e475"
    )


def test_missing_legacy_trigger_is_unknown_not_cli() -> None:
    provenance = legacy_projection_provenance({})

    assert provenance["source"] == "unknown"
    assert provenance["assurance"] == "legacy_unknown"
