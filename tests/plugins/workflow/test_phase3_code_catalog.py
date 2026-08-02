from __future__ import annotations

import hashlib
import json

import pytest

import plugins.workflow.language_schema as language_schema
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.execution_semantics import WorkflowExecutionSemanticsError
from plugins.workflow.conditions import (
    WorkflowConditionError,
    evaluate_v3_condition,
    validate_v3_condition_syntax,
)
from plugins.workflow.language import WorkflowLanguageCompatibilityError
from plugins.workflow.language_schema import compatibility_code_catalog
from plugins.workflow.output_resolution import (
    ResolvedNodeOutput,
    WorkflowOutputReferenceError,
    resolve_output_reference,
)
from plugins.workflow.models import (
    WorkflowLanguageProfile,
    WorkflowValidationError,
)
from plugins.workflow.schema import load_workflow
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import RunStore
from plugins.workflow.trust import compute_package_digest


def test_phase3_durable_code_metadata_is_unique_bounded_and_versioned() -> None:
    codes = [item.code for item in language_schema.PHASE3_DURABLE_CODES]

    assert len(codes) == len(set(codes))
    assert all(
        item.profiles == frozenset({WorkflowLanguageProfile.ARCHON_2026_07})
        and item.normalizer_versions == frozenset({3})
        for item in language_schema.PHASE3_DURABLE_CODES
    )
    assert len(
        json.dumps([item.to_dict() for item in language_schema.PHASE3_DURABLE_CODES])
    ) < 8_192


def test_runtime_only_codes_omit_editor_only_compatibility_projection_fields() -> None:
    """Catch bounded runtime metadata acquiring irrelevant migration/editor prose."""
    catalog = compatibility_code_catalog(WorkflowLanguageProfile.ARCHON_2026_07)
    editor_only = {
        "migration",
        "runtime_status",
        "severity",
        "blocking",
        "enforcement_phase",
    }

    for code in language_schema.PHASE3_DURABLE_CODES:
        projected = catalog[code.code]
        if not code.compatibility:
            assert not editor_only & set(projected)
        assert projected["description"] == code.public_meaning
        assert projected["status"] == "deferred"
        assert projected["profiles"] == ["archon-2026-07"]
        assert projected["normalizer_versions"] == [3]


_DURABLE_BEHAVIOR_CASES = (
        (
            {"id": "approval", "approval": {"message": "Continue?"}, "timeout": 1},
            "archon_timeout_node_unsupported",
        ),
        (
            {"id": "cancel", "cancel": "stop", "idle_timeout": 1},
            "archon_idle_timeout_node_unsupported",
        ),
        (
            {"id": "approval", "approval": {"message": "Continue?"}, "retry": {}},
            "archon_retry_node_unsupported",
        ),
        (
            {"id": "shell", "bash": "true", "retry": {}},
            "archon_retry_max_attempts_required",
        ),
        (
            {"id": "shell", "bash": "true", "timeout": 0},
            "archon_timeout_invalid",
        ),
        (
            {"id": "agent", "prompt": "work", "idle_timeout": False},
            "archon_idle_timeout_invalid",
        ),
        (
            {"id": "agent", "prompt": "work", "retry": {"future": 1}},
            "archon_retry_invalid",
        ),
)


@pytest.mark.parametrize(
    ("node", "expected"),
    _DURABLE_BEHAVIOR_CASES,
)
def test_phase3_catalog_metadata_matches_real_normalization_failure(
    tmp_path, workflow_writer, node, expected
) -> None:
    path = workflow_writer(
        tmp_path,
        nodes=[node],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(path)

    emitted = exc.value.issues[0].code
    catalog = compatibility_code_catalog(WorkflowLanguageProfile.ARCHON_2026_07)
    assert emitted == expected
    assert catalog[emitted]["area"] == "normalization"
    assert catalog[emitted]["normalizer_versions"] == [3]
    assert catalog[emitted]["runtime_failure"] is True
    assert catalog[emitted]["evidence"] is False


def test_every_task1_normalization_behavior_has_catalog_metadata() -> None:
    catalog_codes = {item.code for item in language_schema.PHASE3_DURABLE_CODES}
    behavior_codes = {expected for _node, expected in _DURABLE_BEHAVIOR_CASES}

    assert behavior_codes <= catalog_codes


def test_task2_snapshot_mismatch_codes_have_additive_catalog_metadata() -> None:
    catalog = compatibility_code_catalog(WorkflowLanguageProfile.ARCHON_2026_07)

    for code in (
        "workflow_language_snapshot_mismatch",
        "workflow_execution_semantics_mismatch",
    ):
        assert catalog[code]["area"] == "normalization"
        assert catalog[code]["normalizer_versions"] == [3]
        assert catalog[code]["runtime_failure"] is True
        assert catalog[code]["evidence"] is False


def test_task3_static_reference_codes_have_additive_catalog_metadata() -> None:
    catalog = compatibility_code_catalog(WorkflowLanguageProfile.ARCHON_2026_07)

    for code in (
        "archon_node_id_not_reference_safe",
        "output_reference_not_declared_dependency",
        "output_reference_path_unsupported",
        "structured_output_field_impossible",
        "named_script_output_reference_unsupported",
        "invalid_command_resource",
    ):
        assert catalog[code]["normalizer_versions"] == [3]
        assert catalog[code]["runtime_failure"] is True
        assert catalog[code]["evidence"] is False


def test_task3_catalog_codes_are_emitted_by_real_admission_paths(
    tmp_path, workflow_writer
) -> None:
    emitted: set[str] = set()

    unsafe = workflow_writer(
        tmp_path / "unsafe",
        nodes=[{"id": "unsafe.id", "bash": "true"}],
    )
    unsafe.with_name(f"{unsafe.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    with pytest.raises(WorkflowValidationError) as unsafe_exc:
        load_workflow(unsafe)
    emitted.add(unsafe_exc.value.issues[0].code)

    undeclared = workflow_writer(
        tmp_path / "undeclared",
        nodes=[
            {"id": "producer", "prompt": "produce"},
            {"id": "consumer", "prompt": "$producer.output"},
        ],
    )
    undeclared.with_name(f"{undeclared.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    with pytest.raises(WorkflowValidationError) as undeclared_exc:
        load_workflow(undeclared)
    emitted.add(undeclared_exc.value.issues[0].code)

    unsupported = workflow_writer(
        tmp_path / "unsupported",
        nodes=[
            {"id": "producer", "prompt": "produce"},
            {
                "id": "consumer",
                "prompt": "$producer.output.field",
                "depends_on": ["producer"],
            },
        ],
    )
    unsupported.with_name(f"{unsupported.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    with pytest.raises(WorkflowValidationError) as unsupported_exc:
        load_workflow(unsupported)
    emitted.add(unsupported_exc.value.issues[0].code)

    impossible = workflow_writer(
        tmp_path / "impossible",
        nodes=[
            {
                "id": "producer",
                "prompt": "produce",
                "output_format": {
                    "type": "object",
                    "properties": {"present": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            {
                "id": "consumer",
                "prompt": "$producer.output.missing",
                "depends_on": ["producer"],
            },
        ],
    )
    impossible.with_name(f"{impossible.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    with pytest.raises(WorkflowValidationError) as impossible_exc:
        load_workflow(impossible)
    emitted.add(impossible_exc.value.issues[0].code)

    named_root = tmp_path / "named"
    (named_root / "scripts").mkdir(parents=True)
    (named_root / "scripts" / "consume.py").write_text(
        "print('$producer.output')\n", encoding="utf-8"
    )
    named = workflow_writer(
        named_root,
        nodes=[
            {"id": "producer", "prompt": "produce"},
            {
                "id": "consumer",
                "script": "consume.py",
                "runtime": "uv",
                "depends_on": ["producer"],
            },
        ],
    )
    named.with_name(f"{named.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    with pytest.raises(WorkflowValidationError) as named_exc:
        compute_package_digest(load_workflow(named))
    emitted.add(named_exc.value.issues[0].code)

    invalid_command_root = tmp_path / "invalid-command"
    (invalid_command_root / "commands").mkdir(parents=True)
    (invalid_command_root / "commands" / "consume.md").write_bytes(b"\xff")
    invalid_command = workflow_writer(
        invalid_command_root,
        nodes=[{"id": "consumer", "command": "consume"}],
    )
    invalid_command.with_name(
        f"{invalid_command.stem}.hermes.yaml"
    ).write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    with pytest.raises(WorkflowValidationError) as invalid_command_exc:
        compute_package_digest(load_workflow(invalid_command))
    emitted.add(invalid_command_exc.value.issues[0].code)

    assert emitted == {
        "archon_node_id_not_reference_safe",
        "output_reference_not_declared_dependency",
        "output_reference_path_unsupported",
        "structured_output_field_impossible",
        "named_script_output_reference_unsupported",
        "invalid_command_resource",
    }


def test_task4_runtime_reference_codes_have_behavior_linked_catalog_entries() -> None:
    canonical = b'{"items":[1],"scalar":3}'
    structured = ResolvedNodeOutput(
        canonical_bytes=canonical,
        value={"items": [1], "scalar": 3},
        text=canonical.decode("utf-8"),
        media_type="application/json",
        sha256=hashlib.sha256(canonical).hexdigest(),
        node_id="producer",
        attempt_id="attempt-winner",
        publication_id="a" * 32,
        schema_fingerprint="3" * 64,
        canonicalization_version=1,
    )
    text = b'{"looks":"structured"}'
    schemaless = ResolvedNodeOutput(
        canonical_bytes=text,
        value=text.decode("utf-8"),
        text=text.decode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        sha256=hashlib.sha256(text).hexdigest(),
        node_id="producer",
        attempt_id="attempt-winner",
        publication_id="b" * 32,
    )
    cases = (
        (None, (), "producer", "output_reference_missing"),
        (schemaless, ("looks",), "producer", "output_reference_not_structured"),
        (structured, ("missing",), "producer", "output_reference_field_missing"),
        (structured, ("scalar", "child"), "producer", "output_reference_path_type"),
        (structured, (), "different", "output_reference_integrity"),
    )
    emitted: set[str] = set()
    for output, path, node_id, expected in cases:
        with pytest.raises(WorkflowOutputReferenceError) as exc:
            resolve_output_reference(output, node_id=node_id, path=path)
        assert exc.value.code == expected
        emitted.add(exc.value.code)

    catalog = compatibility_code_catalog(WorkflowLanguageProfile.ARCHON_2026_07)
    for code in emitted:
        assert catalog[code]["area"] == "references"
        assert catalog[code]["normalizer_versions"] == [3]
        assert catalog[code]["runtime_failure"] is True
        assert catalog[code]["evidence"] is False


def test_task5_condition_codes_have_behavior_linked_catalog_entries() -> None:
    canonical = b'"2"'
    structured_string = ResolvedNodeOutput(
        canonical_bytes=canonical,
        value="2",
        text=canonical.decode("utf-8"),
        media_type="application/json",
        sha256=hashlib.sha256(canonical).hexdigest(),
        node_id="producer",
        attempt_id="attempt-winner",
        publication_id="c" * 32,
        schema_fingerprint="4" * 64,
    )
    invalid_numeric = b"not-a-number"
    schemaless = ResolvedNodeOutput(
        canonical_bytes=invalid_numeric,
        value=invalid_numeric.decode("utf-8"),
        text=invalid_numeric.decode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        sha256=hashlib.sha256(invalid_numeric).hexdigest(),
        node_id="producer",
        attempt_id="attempt-winner",
        publication_id="d" * 32,
    )
    finite = ResolvedNodeOutput(
        canonical_bytes=b"1",
        value=1,
        text="1",
        media_type="application/json",
        sha256=hashlib.sha256(b"1").hexdigest(),
        node_id="producer",
        attempt_id="attempt-winner",
        publication_id="e" * 32,
        schema_fingerprint="5" * 64,
    )
    object.__setattr__(finite, "value", float("inf"))

    emitters = (
        lambda: validate_v3_condition_syntax("$producer.output"),
        lambda: evaluate_v3_condition(
            "$producer.output == 2", {"producer": structured_string}
        ),
        lambda: evaluate_v3_condition(
            "$producer.output > 1", {"producer": finite}
        ),
        lambda: evaluate_v3_condition(
            "$producer.output > 1", {"producer": schemaless}
        ),
    )
    emitted: set[str] = set()
    for emit in emitters:
        with pytest.raises(WorkflowConditionError) as exc:
            emit()
        emitted.add(exc.value.code)

    assert emitted == {
        "condition_operand_type",
        "condition_operand_nonfinite",
        "condition_numeric_invalid",
        "condition_runtime_syntax_invalid",
    }
    catalog = compatibility_code_catalog(WorkflowLanguageProfile.ARCHON_2026_07)
    for code in emitted:
        assert catalog[code]["area"] == "conditions"
        assert catalog[code]["normalizer_versions"] == [3]
        assert catalog[code]["runtime_failure"] is True
        assert catalog[code]["evidence"] is False


def _admit_catalog_snapshot(store: RunStore, package, *, key: str) -> str:
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=key,
            concurrency_key=key,
            execution_mode="foreground",
            foreground_owner_id=f"owner-{key}",
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    return admitted.run_id


def _reseal_catalog_resources(
    store: RunStore,
    run_id: str,
    resources: dict[str, object],
    *,
    projection_updates: dict[str, object] | None = None,
) -> None:
    run_directory = store.run_directory(run_id)
    encoded = json.dumps(resources, sort_keys=True, separators=(",", ":")).encode()
    (run_directory / "resources.json").write_bytes(encoded)
    from plugins.workflow.scheduled_revalidation import sealed_snapshot_digest

    store.append_event(
        run_id,
        "test_reseal_catalog_resources",
        projection_updates={
            **dict(projection_updates or {}),
            "input_manifest_digest": hashlib.sha256(encoded).hexdigest(),
            "sealed_snapshot_digest": sealed_snapshot_digest(run_directory),
        },
    )


def test_task2_catalog_codes_are_emitted_by_real_resume_failures(
    tmp_path, workflow_writer
) -> None:
    path = workflow_writer(
        tmp_path,
        nodes=[{"id": "agent", "prompt": "work"}],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(path)
    store = RunStore(tmp_path / "home")
    language_run = _admit_catalog_snapshot(store, package, key="language-mismatch")
    language_resources_path = store.run_directory(language_run) / "resources.json"
    language_resources = json.loads(language_resources_path.read_bytes())
    language_resources["language"]["normalized_definition_digest"] = "b" * 64
    _reseal_catalog_resources(
        store,
        language_run,
        language_resources,
        projection_updates={"language": language_resources["language"]},
    )

    scheduler = RunScheduler(store)
    try:
        with pytest.raises(WorkflowLanguageCompatibilityError) as language_exc:
            scheduler._prepare_run_package(language_run, None)

        execution_run = _admit_catalog_snapshot(
            store, package, key="execution-mismatch"
        )
        execution_resources_path = (
            store.run_directory(execution_run) / "resources.json"
        )
        execution_resources = json.loads(execution_resources_path.read_bytes())
        execution_resources["phase3_execution_semantics"]["nodes"]["agent"][
            "retry"
        ]["effective_total_attempts"] = 1
        _reseal_catalog_resources(store, execution_run, execution_resources)
        with pytest.raises(WorkflowExecutionSemanticsError) as execution_exc:
            scheduler._prepare_run_package(execution_run, None)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert {
        language_exc.value.code,
        execution_exc.value.code,
    } == {
        "workflow_language_snapshot_mismatch",
        "workflow_execution_semantics_mismatch",
    }
