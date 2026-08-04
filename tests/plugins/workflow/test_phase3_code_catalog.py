from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os

import pytest

from agent.plugin_agent import PluginAgentRunResult, PluginAgentSessionMissingError
import plugins.workflow.bash_rendering as bash_rendering
import plugins.workflow.language_schema as language_schema
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.bash_rendering import BashRenderingError, render_v3_bash
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
from plugins.workflow.sessions import NodeSessionRegistry
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
    projected = compatibility_code_catalog(
        WorkflowLanguageProfile.ARCHON_2026_07
    )
    canonical_bytes = json.dumps(
        projected,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    assert len(canonical_bytes) <= (
        language_schema.PHASE3_DURABLE_CODE_CATALOG_MAX_BYTES
    )


def test_api_finding_migrations_use_the_versioned_editor_code_authority() -> None:
    from plugins.workflow.dashboard.plugin_api import _finding_migration

    catalog = compatibility_code_catalog(WorkflowLanguageProfile.HERMES_LEGACY)

    assert _finding_migration(
        "legacy_language_profile", WorkflowLanguageProfile.HERMES_LEGACY.value
    ) == catalog["legacy_language_profile"]["migration"]


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

def _emit_bash_catalog_codes(
    tmp_path, *, platform_name: str | None = None
) -> set[str]:
    platform_name = os.name if platform_name is None else platform_name
    template = "$producer.output"
    span = (0, len(template))
    emitted: set[str] = set()
    for index, value in enumerate(("before\0after", "x" * 500_001)):
        with pytest.raises(BashRenderingError) as exc:
            render_v3_bash(
                template,
                [(span[0], span[1], value)],
                spill_directory=tmp_path / f"bounds-{index}",
            )
        emitted.add(exc.value.code)

    context = "printf '%s' $(( $producer.output ))"
    start = context.index("$producer.output")
    with pytest.raises(BashRenderingError) as context_exc:
        render_v3_bash(
            context,
            [(start, start + len(template), "2")],
            spill_directory=tmp_path / "context",
        )
    emitted.add(context_exc.value.code)

    if platform_name != "nt":
        escape = tmp_path / "spill-escape"
        (escape / "attempt").mkdir(parents=True)
        alias = tmp_path / "spill-alias"
        alias.symlink_to(escape, target_is_directory=True)
        with pytest.raises(BashRenderingError) as spill_exc:
            render_v3_bash(
                template,
                [(span[0], span[1], "x" * 32_769)],
                spill_directory=alias / "attempt" / "variables-v3",
            )
        emitted.add(spill_exc.value.code)
    else:
        original_native_windows = bash_rendering._NATIVE_WINDOWS
        bash_rendering._NATIVE_WINDOWS = True
        try:
            with pytest.raises(BashRenderingError) as spill_exc:
                render_v3_bash(
                    template,
                    [(span[0], span[1], "x" * 32_769)],
                    spill_directory=tmp_path / "windows-spill",
                )
        finally:
            bash_rendering._NATIVE_WINDOWS = original_native_windows
        emitted.add(spill_exc.value.code)
    return emitted


class _CatalogSessionRunner:
    def __init__(self) -> None:
        self.requests = []
        self.shared_failure: BaseException | None = None

    def run(self, request, **_kwargs):
        self.requests.append(request)
        if request.context_mode == "shared" and self.shared_failure is not None:
            raise self.shared_failure
        return PluginAgentRunResult(
            final_response="ok",
            session_id=f"catalog-session-{len(self.requests)}",
            provider="catalog-provider",
            model="catalog-model",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={"provider_attempts": 1},
        )


def _catalog_session_package(workflow_writer, root, *, name, nodes=None):
    path = workflow_writer(
        root,
        name=name,
        persist_sessions=True,
        provider="catalog-provider",
        model="catalog-model",
        nodes=nodes or [{"id": "analyze", "prompt": "Analyze"}],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    return load_workflow(path)


def _run_catalog_session(store, package, scheduler, key):
    run_id = _admit_catalog_snapshot(store, package, key=key)
    return run_id, scheduler.advance(run_id)


def _emit_session_catalog_codes(tmp_path, workflow_writer) -> set[str]:
    emitted: set[str] = set()
    same_package = _catalog_session_package(
        workflow_writer,
        tmp_path / "same-package",
        name="same-session",
        nodes=[
            {"id": "first", "prompt": "First"},
            {
                "id": "second",
                "prompt": "Second",
                "depends_on": ["first"],
                "context": "shared",
            },
        ],
    )
    same_home = tmp_path / "same-home"
    same_store = RunStore(same_home)
    same_runner = _CatalogSessionRunner()
    same_runner.shared_failure = PluginAgentSessionMissingError("confirmed absent")
    same_scheduler = RunScheduler(
        same_store,
        agent_runner=same_runner,
        session_registry=NodeSessionRegistry(same_home),
    )
    try:
        _same_id, same = _run_catalog_session(
            same_store, same_package, same_scheduler, "same-run"
        )
    finally:
        same_scheduler.shutdown(deadline_seconds=2)
    emitted.add(same["nodes"]["second"]["attempts"][0]["error_code"])

    package = _catalog_session_package(
        workflow_writer, tmp_path / "cross-package", name="cross-session"
    )
    home = tmp_path / "cross-home"
    store = RunStore(home)
    registry = NodeSessionRegistry(home)
    runner = _CatalogSessionRunner()
    scheduler = RunScheduler(
        store, agent_runner=runner, session_registry=registry
    )
    try:
        _run_catalog_session(store, package, scheduler, "seed")
        runner.shared_failure = OSError("registry probe unavailable")
        _unavailable_id, unavailable = _run_catalog_session(
            store, package, scheduler, "unavailable"
        )
        emitted.add(unavailable["nodes"]["analyze"]["attempts"][0]["error_code"])
        runner.shared_failure = PluginAgentSessionMissingError("confirmed absent")
        recovered_id, recovered = _run_catalog_session(
            store, package, scheduler, "recovered"
        )
    finally:
        scheduler.shutdown(deadline_seconds=2)
    assert recovered["status"] == "succeeded"
    emitted.add(
        next(
            event["event_type"]
            for event in store.tail_events(recovered_id)
            if event["event_type"] == "persistent_session_missing_fresh_start"
        )
    )

    class UnavailableRegistry(NodeSessionRegistry):
        def compare_and_set_or_observe(self, *_args, **_kwargs):
            raise OSError("registry update unavailable")

    pending_home = tmp_path / "pending-home"
    pending_store = RunStore(pending_home)
    pending_registry = UnavailableRegistry(pending_home)
    pending_runner = _CatalogSessionRunner()
    pending_id = _admit_catalog_snapshot(
        pending_store, package, key="pending-update"
    )
    clock = [datetime(2026, 8, 1, tzinfo=timezone.utc)]
    scheduler = RunScheduler(
        pending_store,
        agent_runner=pending_runner,
        session_registry=pending_registry,
        utcnow=lambda: clock[0],
    )
    try:
        scheduler.advance(pending_id)
        for delay in (1, 2, 4, 8):
            clock[0] += timedelta(seconds=delay)
            scheduler.advance(pending_id)
    finally:
        scheduler.shutdown(deadline_seconds=2)
    emitted.add(pending_store.load_run(pending_id)["last_error"]["code"])
    return emitted


def test_every_registered_phase3_code_has_an_executable_emitter(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    """Pair the complete registry with real behavior, not catalog prose."""
    shutdown_deadlines: list[float] = []
    original_shutdown = RunScheduler.shutdown

    def tracking_shutdown(self, deadline_seconds=10):
        shutdown_deadlines.append(deadline_seconds)
        return original_shutdown(self, deadline_seconds=deadline_seconds)

    monkeypatch.setattr(RunScheduler, "shutdown", tracking_shutdown)
    observed: set[str] = set()
    for index, (node, expected) in enumerate(_DURABLE_BEHAVIOR_CASES):
        observed.add(
            _emit_normalization_failure(
                tmp_path / f"normalization-{index}", workflow_writer, node, expected
            )
        )
    observed.update(_emit_resume_failure_codes(tmp_path / "resume", workflow_writer))
    observed.update(
        _emit_static_admission_codes(tmp_path / "admission", workflow_writer)
    )
    observed.update(_emit_runtime_reference_codes())
    observed.update(_emit_condition_codes())
    observed.update(_emit_resolution_wait_codes(tmp_path / "waits", workflow_writer))
    observed.update(_emit_bash_catalog_codes(tmp_path / "bash"))
    observed.update(_emit_session_catalog_codes(tmp_path / "sessions", workflow_writer))

    assert observed == {item.code for item in language_schema.PHASE3_DURABLE_CODES}
    assert shutdown_deadlines == [2] * 4


def test_bash_catalog_windows_path_emits_integrity_without_skipping(
    tmp_path,
) -> None:
    try:
        emitted = _emit_bash_catalog_codes(tmp_path, platform_name="nt")
    except pytest.skip.Exception:
        pytest.fail("the native-Windows Bash catalog path must not be skipped")

    assert "bash_spill_integrity" in emitted


@pytest.mark.parametrize(
    ("node", "expected"),
    _DURABLE_BEHAVIOR_CASES,
)
def _emit_normalization_failure(
    tmp_path, workflow_writer, node, expected
) -> str:
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
    return emitted


@pytest.mark.parametrize(("node", "expected"), _DURABLE_BEHAVIOR_CASES)
def test_phase3_catalog_metadata_matches_real_normalization_failure(
    tmp_path, workflow_writer, node, expected
) -> None:
    assert _emit_normalization_failure(tmp_path, workflow_writer, node, expected)


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


def _emit_static_admission_codes(
    tmp_path, workflow_writer
) -> set[str]:
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
    return emitted


def test_task3_catalog_codes_are_emitted_by_real_admission_paths(
    tmp_path, workflow_writer
) -> None:
    assert len(_emit_static_admission_codes(tmp_path, workflow_writer)) == 6


def _emit_runtime_reference_codes() -> set[str]:
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
    return emitted


def test_task4_runtime_reference_codes_have_behavior_linked_catalog_entries() -> None:
    assert len(_emit_runtime_reference_codes()) == 5


def _emit_condition_codes() -> set[str]:
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
    return emitted


def test_task5_condition_codes_have_behavior_linked_catalog_entries() -> None:
    assert len(_emit_condition_codes()) == 4


def test_task11_bash_codes_have_bounded_runtime_catalog_entries() -> None:
    catalog = compatibility_code_catalog(WorkflowLanguageProfile.ARCHON_2026_07)

    for code in (
        "bash_substitution_nul",
        "bash_substitution_limit",
        "bash_spill_integrity",
        "bash_reference_context_unsupported",
    ):
        assert catalog[code]["area"] == "bash"
        assert catalog[code]["normalizer_versions"] == [3]
        assert catalog[code]["runtime_failure"] is True
        assert catalog[code]["evidence"] is False
        assert catalog[code]["fields"] == ["nodes[].bash"]


def _emit_resolution_wait_codes(
    tmp_path, workflow_writer
) -> set[str]:
    """Catch catalog-only transient/exhausted codes with no durable emitter."""
    path = workflow_writer(
        tmp_path / "resolution-catalog",
        name="resolution-catalog",
        nodes=[
            {"id": "producer", "bash": "true"},
            {"id": "consumer", "bash": "true", "depends_on": ["producer"]},
        ],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(path)
    store = RunStore(tmp_path / "resolution-catalog-home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="resolution-catalog",
            concurrency_key="resolution-catalog",
        ),
        immutable_snapshot=prepared,
    )
    identity = {
        "node_id": "producer",
        "attempt_id": "attempt-winner",
        "publication_id": "a" * 32,
        "sha256": "b" * 64,
        "size_bytes": 5,
        "media_type": "text/markdown; charset=utf-8",
        "schema_fingerprint": None,
        "canonicalization_version": 1,
        "output_type": "text",
    }
    observed = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    for read_count in range(1, 7):
        assert store.defer_output_resolution(
            admitted.run_id,
            "consumer",
            producer_identity=identity,
            now=observed,
        )
        if read_count < 6:
            due = datetime.fromisoformat(
                store.load_run(admitted.run_id)["nodes"]["consumer"][
                    "next_resolution_at"
                ]
            )
            assert store.wake_due_output_resolutions(
                admitted.run_id, now=due
            ) == ("consumer",)
            observed = due

    transient = next(
        event["payload"]["error_code"]
        for event in store.tail_events(admitted.run_id)
        if event["event_type"] == "output_resolution_deferred"
    )
    exhausted = store.load_run(admitted.run_id)["last_error"]["code"]
    assert {transient, exhausted} == {
        "output_reference_temporarily_unavailable",
        "output_reference_unavailable",
    }

    catalog = compatibility_code_catalog(WorkflowLanguageProfile.ARCHON_2026_07)
    for code in (transient, exhausted):
        assert catalog[code]["area"] == "references"
        assert catalog[code]["normalizer_versions"] == [3]
        assert catalog[code]["runtime_failure"] is True
        assert catalog[code]["evidence"] is False
    return {transient, exhausted}


def test_task6_resolution_wait_codes_have_real_state_machine_emitters(
    tmp_path, workflow_writer
) -> None:
    assert len(_emit_resolution_wait_codes(tmp_path, workflow_writer)) == 2


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


def _emit_resume_failure_codes(
    tmp_path, workflow_writer
) -> set[str]:
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

    emitted = {
        language_exc.value.code,
        execution_exc.value.code,
    }
    assert emitted == {
        "workflow_language_snapshot_mismatch",
        "workflow_execution_semantics_mismatch",
    }
    return emitted


def test_task2_catalog_codes_are_emitted_by_real_resume_failures(
    tmp_path, workflow_writer
) -> None:
    assert len(_emit_resume_failure_codes(tmp_path, workflow_writer)) == 2
