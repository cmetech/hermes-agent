from __future__ import annotations

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.bash_rendering import bash_output_references
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.executors.bash import BashExecutor
from plugins.workflow.language_schema import (
    WorkflowReferenceSyntaxError,
    iter_output_references_in_spans,
)
from plugins.workflow.models import WorkflowLanguageProfile, WorkflowNode, freeze_value
from plugins.workflow.output_resolution import WorkflowOutputReferenceError
from plugins.workflow.resources import VariableContext, substitution_renderer
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


_LITERAL_MALFORMED_REFERENCES = (
    "$producer.output[0]",
    "$producer/output",
    "$producer.output.bad/path",
    "$producer.output..field",
)


def _archon_literal_package(workflow_writer, root, token: str):
    workflow = workflow_writer(
        root,
        name="literal-malformed-bash-reference",
        nodes=[
            {
                "id": "shell",
                "bash": f"printf '%s' \"\\{token}\"\n# {token}",
            }
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    return load_workflow(workflow)


def _start(store: RunStore, package, *, key: str):
    prepared = store.prepare_run_snapshot(package)
    return store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=key,
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )


def test_v3_admission_filters_literal_context_before_strict_reference_grammar(
    tmp_path,
    workflow_writer,
) -> None:
    for index, token in enumerate(_LITERAL_MALFORMED_REFERENCES):
        package = _archon_literal_package(
            workflow_writer,
            tmp_path / f"package-{index}",
            token,
        )

        assert package.definition.nodes[0].depends_on == ()


def test_v3_direct_executor_filters_literal_context_before_reference_resolution(
    tmp_path,
) -> None:
    token = "$producer.output[0]"
    node = WorkflowNode(
        id="shell",
        node_type="bash",
        value=f"printf '%s' \"\\{token}\"\n# $producer/output",
        depends_on=(),
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )

    result = BashExecutor().execute(
        NodeExecutionContext(
            run_id="literal-malformed-direct",
            run_directory=tmp_path,
            node=node,
            attempt_id="attempt-1",
            variable_context=VariableContext(normalizer_version=3),
            language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=3,
        )
    )

    assert result.status == "succeeded"
    assert (tmp_path / "nodes" / "shell" / "attempt-1" / "stdout.txt").read_text(
        encoding="utf-8"
    ) == token


def test_v3_scheduler_filters_literal_context_before_reference_preflight(
    tmp_path,
    workflow_writer,
) -> None:
    token = "$producer.output.bad/path"
    package = _archon_literal_package(
        workflow_writer,
        tmp_path / "package",
        token,
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package, key="literal-malformed-scheduler")

    result = RunScheduler(store).advance(admitted.run_id)

    assert result["status"] == "succeeded", result.get("last_error")
    stdout = next(
        artifact
        for artifact in result["artifacts"]
        if artifact["node_id"] == "shell" and "stdout" in artifact["relative_path"]
    )
    assert (store.run_directory(admitted.run_id) / stdout["relative_path"]).read_text(
        encoding="utf-8"
    ) == token


def test_lexical_reference_parser_cannot_read_past_an_admitted_span() -> None:
    template = "$producer.output"

    assert (
        tuple(
            iter_output_references_in_spans(
                template,
                ((0, 1),),
                normalizer_version=3,
            )
        )
        == ()
    )


def test_bounded_bash_reference_error_preserves_its_template_offset() -> None:
    prefix = "printf x; "

    with pytest.raises(WorkflowReferenceSyntaxError) as exc:
        bash_output_references(f"{prefix}$bad.output-field")

    assert exc.value.start == len(prefix)


def test_logical_line_continuations_preserve_physical_reference_offsets() -> None:
    prefix = "printf first \\\ncontinued; printf second; "

    with pytest.raises(WorkflowReferenceSyntaxError) as exc:
        bash_output_references(f"{prefix}$bad.output-field")

    assert exc.value.start == len(prefix)


def test_joined_multi_heredoc_mapping_preserves_the_authored_reference_offset() -> None:
    prefix = (
        ": 3<\\\n<-\\\n'ONE' 4<<-\\\n\"TWO\"\n"
        "\tone\\\n\tONE\n\ttwo\\\n\tTWO\n"
        "printf '%s' "
    )

    with pytest.raises(WorkflowReferenceSyntaxError) as exc:
        bash_output_references(f"{prefix}$bad.output-field")

    assert exc.value.start == len(prefix)


def test_bounded_bash_reference_error_reports_the_exact_producer(tmp_path) -> None:
    renderer = substitution_renderer(
        VariableContext(normalizer_version=3),
        direct_dependencies=("bad",),
    )
    spill_directory = tmp_path / "malformed-spill"

    with pytest.raises(WorkflowOutputReferenceError) as exc:
        renderer.render_bash(
            "printf x; $bad.output-field",
            spill_directory=spill_directory,
            secure_v3=True,
        )

    assert exc.value.code == "output_reference_path_unsupported"
    assert exc.value.node_id == "bad"
    assert not spill_directory.exists()
