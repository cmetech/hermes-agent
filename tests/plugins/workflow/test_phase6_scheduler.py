from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
import hashlib
from pathlib import Path
import threading

import pytest

from hermes_cli.runtime_provider import classify_execution_runtime
from hermes_cli.workflow_model_resolution import parse_workflow_model_config
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.entitlement import AIEntitlementResolution
from plugins.workflow.executors.base import NodeExecutionResult
from plugins.workflow.output_resolution import (
    PrimaryOutputCandidate,
    WorkflowOutputReferenceError,
)
from plugins.workflow.provider_authority import ProviderAuthorityEnvironment
from plugins.workflow.resources import VariableContext, substitution_renderer
from plugins.workflow.runner_binding import (
    RunnerCapabilities,
    execution_capability_context,
)
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import parse_workflow_source_bytes
from plugins.workflow.store import ArtifactRef, RunStore
from plugins.workflow.trust import WorkflowPackageDigest


_STRUCTURED_OUTPUT = {
    "type": "object",
    "properties": {
        "present": {"type": "string"},
        "missing": {"type": "string"},
    },
    "required": ["present"],
    "additionalProperties": False,
}


def _execution_context():
    model_config = parse_workflow_model_config({
        "model": {
            "provider": "openrouter",
            "default": "openai/gpt-5.4",
            "base_url": "https://openrouter.ai/api/v1",
        }
    })
    runtime = classify_execution_runtime(
        provider="openrouter",
        model_config={
            "provider": "openrouter",
            "default": "openai/gpt-5.4",
        },
        provider_config={"base_url": "https://openrouter.ai/api/v1"},
    )
    return execution_capability_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
        runner_capabilities=RunnerCapabilities(starts_request_mcp=True),
        runtime_capabilities=runtime,
        model_config_snapshot=model_config,
        provider_authority_environment=ProviderAuthorityEnvironment(
            session_store_available=True,
            mcp_available=True,
            hook_lifecycle_available=True,
            inline_agent_available=True,
            web_service_available=True,
            authoritative_cost_available=True,
        ),
    )


def _compile(tmp_path: Path, workflow_writer, *, name: str, nodes, **options):
    root = tmp_path / name
    commands = root / "commands"
    commands.mkdir(parents=True)
    (commands / "nested-command.md").write_text("nested command", encoding="utf-8")
    workflow = workflow_writer(
        root / "workflows",
        name=name,
        filename=f"{name}.yaml",
        nodes=nodes,
        **options,
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_bytes(sidecar)
    source = parse_workflow_source_bytes(
        workflow,
        workflow_bytes=workflow.read_bytes(),
        sidecar_bytes=sidecar,
        source="project",
        precedence=1,
    )
    return compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=6,
    )


def _admit(store: RunStore, compilation, *, key: str) -> str:
    context = _execution_context()
    package = compilation.package
    prepared = store.prepare_run_snapshot(
        package,
        compilation=compilation,
        trusted_package_digest=WorkflowPackageDigest(
            compilation.composite_digest,
            compilation.covered_relative_paths,
        ),
        provider_authority=context.provider_authority(package),
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=key,
            concurrency_key=package.definition.name,
            run_metadata=context.structured_output_run_metadata(package),
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    return admitted.run_id


def _group(body, *, maximum: int = 1, depends_on=(), **options):
    return {
        "id": "group",
        "depends_on": list(depends_on),
        "loop_group": {
            "until": "DONE",
            "max_iterations": maximum,
            "nodes": body,
        },
        **options,
    }


def _body_id(context) -> str:
    return context.node.id.rsplit("/", 1)[-1]


class SucceedingExecutor:
    def __init__(self) -> None:
        self.contexts = []

    def execute(self, context):
        self.contexts.append(context)
        return NodeExecutionResult("succeeded")


class OutputExecutor:
    def __init__(self, output) -> None:
        self.output = output
        self.rendered: list[tuple[str, str]] = []

    def execute(self, context):
        node_id = _body_id(context)
        template = context.node.value
        if isinstance(template, str):
            variables = context.variable_context
            assert isinstance(variables, VariableContext)
            template = substitution_renderer(
                variables,
                direct_dependencies=context.node.depends_on,
                output_resolver=context.output_resolver,
            ).render_prompt(template)
            self.rendered.append((node_id, template))
        value = self.output(context, template)
        if isinstance(value, NodeExecutionResult):
            return value
        data = value if isinstance(value, bytes) else str(value).encode()
        structured = context.structured_output is not None
        filename = "output.json" if structured else "output.txt"
        attempt = context.effective_attempt_directory
        attempt.mkdir(parents=True, exist_ok=False)
        path = attempt / filename
        path.write_bytes(data)
        relative = path.relative_to(context.run_directory).as_posix()
        digest = hashlib.sha256(data).hexdigest()
        artifact = ArtifactRef(
            relative,
            "application/json" if structured else "text/plain",
            len(data),
            digest,
        )
        return NodeExecutionResult(
            "succeeded",
            artifacts=(artifact,),
            primary_output=PrimaryOutputCandidate(
                attempt_relative_path=relative,
                media_type=artifact.media_type,
                size_bytes=len(data),
                sha256=digest,
                structured_value=None,
                schema_fingerprint=(
                    context.structured_output.schema_fingerprint
                    if structured
                    else None
                ),
                canonicalization_version=1,
                output_type=None,
            ),
        )


def test_one_worker_uses_one_pool_and_body_source_order(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="one-worker",
        nodes=[
            _group([
                {"id": "z-first", "bash": "true"},
                {"id": "a-second", "bash": "true"},
            ])
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="one-worker")
    executor = SucceedingExecutor()
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["bash"] = executor
    pools = []

    def pool(*args, **kwargs):
        pools.append((args, kwargs))
        return RealThreadPoolExecutor(*args, **kwargs)

    monkeypatch.setattr("plugins.workflow.scheduler.ThreadPoolExecutor", pool)

    result = scheduler.advance_all([run_id])[run_id]

    assert [_body_id(context) for context in executor.contexts] == [
        "z-first",
        "a-second",
    ]
    assert len(pools) == 1
    body = result["nodes"]["group"]["loop_group"]["body"]
    assert {state["state"] for state in body.values()} == {"succeeded"}


def test_group_layer_overlaps_and_claims_stay_under_existing_limits(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="group-parallel",
        nodes=[_group([{"id": "left", "bash": "true"}, {"id": "right", "bash": "true"}])],
    )
    store = RunStore(tmp_path / "home", max_total_workers=2)
    run_id = _admit(store, compilation, key="group-parallel")
    barrier = threading.Barrier(2, timeout=5)
    lock = threading.Lock()
    active = 0
    maximum = 0

    class BlockingExecutor:
        def execute(self, _context):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            barrier.wait()
            with store._connect() as connection:
                assert connection.execute(
                    "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
                ).fetchone()[0] <= 2
                assert connection.execute(
                    "SELECT COUNT(*) FROM worker_claims"
                ).fetchone()[0] <= 2
            with lock:
                active -= 1
            return NodeExecutionResult("succeeded")

    scheduler = RunScheduler(store, max_parallel_nodes=2)
    scheduler.executors["bash"] = BlockingExecutor()

    scheduler.advance_all([run_id])

    assert maximum == 2


def test_bounded_advance_uses_the_same_scoped_work_item_path(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="bounded-advance",
        nodes=[_group([{"id": "first", "bash": "true"}, {"id": "second", "bash": "true"}])],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="bounded-advance")
    executor = SucceedingExecutor()
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["bash"] = executor

    first = scheduler.advance(run_id, max_nodes=1)
    second = scheduler.advance(run_id, max_nodes=1)

    assert [_body_id(context) for context in executor.contexts] == ["first", "second"]
    assert first["nodes"]["group"]["loop_group"]["body"]["first"]["state"] == (
        "succeeded"
    )
    assert second["nodes"]["group"]["loop_group"]["body"]["second"]["state"] == (
        "succeeded"
    )


def test_fair_cursor_rotates_two_groups_and_one_ordinary_run(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_ids = []
    for name in ("group-a", "group-b"):
        compilation = _compile(
            tmp_path,
            workflow_writer,
            name=name,
            nodes=[_group([{"id": f"n{i}", "bash": "true"} for i in range(3)])],
        )
        run_ids.append(_admit(store, compilation, key=name))
    ordinary = _compile(
        tmp_path,
        workflow_writer,
        name="ordinary",
        nodes=[{"id": f"n{i}", "bash": "true"} for i in range(3)],
    )
    run_ids.append(_admit(store, ordinary, key="ordinary"))
    starts = []

    class RecordingExecutor:
        def execute(self, context):
            starts.append(context.workflow_name)
            with store._connect() as connection:
                assert connection.execute(
                    "SELECT COUNT(*) FROM worker_claims"
                ).fetchone()[0] == 1
            return NodeExecutionResult("succeeded")

    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["bash"] = RecordingExecutor()

    scheduler.advance_all(run_ids)

    assert starts[:3] == ["group-a", "group-b", "ordinary"]


def test_next_iteration_claim_follows_committed_previous_iteration(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="iteration-order",
        nodes=[_group([{"id": "body", "bash": "true"}], maximum=2)],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="iteration-order")
    executor = OutputExecutor(lambda _context, _rendered: "body-output")
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["bash"] = executor

    scheduler.advance_all([run_id])

    events = store.tail_events(run_id)
    committed = next(
        event
        for event in events
        if event["event_type"] == "loop_group_iteration_committed"
    )
    second_claim = next(
        event
        for event in events
        if event["event_type"] == "loop_group_child_claimed"
        and event["payload"]["loop_group_scope"]["iteration"] == 2
    )
    first_completed = next(
        event
        for event in events
        if event["event_type"] == "loop_group_child_succeeded"
        and event["payload"]["loop_group_scope"]["iteration"] == 1
    )
    assert first_completed["sequence"] < committed["sequence"] < second_claim["sequence"]


def test_existing_executors_receive_scoped_paths_and_semantic_authority(
    tmp_path, workflow_writer
) -> None:
    body = [
        {"id": "prompt", "prompt": "prompt"},
        {"id": "command", "command": "nested-command"},
        {"id": "bash", "bash": "true"},
        {"id": "script", "script": "print('ok')", "runtime": "uv"},
        {"id": "approval", "approval": {"message": "approve"}},
        {
            "id": "ordinary-loop",
            "loop": {"prompt": "loop", "until": "DONE", "max_iterations": 1},
        },
    ]
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="executor-paths",
        nodes=[_group(body, provider="openrouter", model="openai/gpt-5.4")],
    )
    store = RunStore(tmp_path / "home", max_total_workers=6)
    run_id = _admit(store, compilation, key="executor-paths")
    scheduler = RunScheduler(store, max_parallel_nodes=6)
    executors = {node_type: SucceedingExecutor() for node_type in (
        "prompt", "command", "bash", "script", "approval", "loop"
    )}
    scheduler.executors.update(executors)

    scheduler.advance_all([run_id])

    run_directory = store.run_directory(run_id)
    for node_type, executor in executors.items():
        [context] = executor.contexts
        body_id = "ordinary-loop" if node_type == "loop" else node_type
        assert context.node.id == f"group/{body_id}"
        assert context.node.options["provider"] == "openrouter"
        relative_attempt = context.effective_attempt_directory.relative_to(run_directory)
        assert relative_attempt.parts[:6] == (
            "nodes", "group", "1", "iterations", "0001", "nodes"
        )
        assert relative_attempt.parts[6] == body_id
        assert len(relative_attempt.parts) == 8
        assert context.effective_publication_directory == (
            run_directory
            / "artifacts"
            / "loop-groups"
            / "group"
            / "iterations"
            / "0001"
            / body_id
        )
        assert context.effective_attempt_directory.resolve().is_relative_to(
            run_directory.resolve()
        )


def test_current_outer_and_previous_outputs_stay_in_their_scopes(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="scoped-references",
        nodes=[
            {"id": "outer", "prompt": "outer"},
            {"id": "hidden", "prompt": "hidden"},
            _group(
                [
                    {"id": "current", "prompt": "current"},
                    {
                        "id": "consumer",
                        "prompt": (
                            "[$current.output]|[$outer.output]|"
                            "[$LOOP_PREV.current.output]"
                        ),
                        "depends_on": ["current"],
                    },
                ],
                maximum=2,
                depends_on=("outer",),
            ),
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=2)
    run_id = _admit(store, compilation, key="scoped-references")

    def output(context, rendered):
        node_id = _body_id(context)
        iteration = (
            2 if "iterations/0002" in context.effective_attempt_directory.as_posix() else 1
        )
        if node_id == "current":
            return f"current-{iteration}"
        if context.node.id == "outer":
            return "outer"
        if context.node.id == "hidden":
            return "hidden"
        assert isinstance(context.variable_context, VariableContext)
        with pytest.raises(WorkflowOutputReferenceError):
            context.variable_context.output_reference("hidden")
        return rendered

    executor = OutputExecutor(output)
    scheduler = RunScheduler(store, max_parallel_nodes=2)
    scheduler.executors["prompt"] = executor

    scheduler.advance_all([run_id])

    assert [rendered for node_id, rendered in executor.rendered if node_id == "consumer"] == [
        "[current-1]|[outer]|[]",
        "[current-2]|[outer]|[current-1]",
    ]


def test_previous_structured_field_fails_before_child_execution(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="previous-field",
        nodes=[
            _group([
                {
                    "id": "producer",
                    "prompt": "producer",
                    "output_format": _STRUCTURED_OUTPUT,
                },
                {
                    "id": "consumer",
                    "prompt": "$LOOP_PREV.producer.output.missing",
                    "depends_on": ["producer"],
                },
            ])
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="previous-field")

    def output(context, rendered):
        if _body_id(context) == "producer":
            return b'{"present":"yes"}'
        raise AssertionError(f"consumer executed with {rendered}")

    executor = OutputExecutor(output)
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["prompt"] = executor

    result = scheduler.advance_all([run_id])[run_id]

    assert result["status"] == "failed"
    assert result["last_error"]["code"] == "output_reference_missing"


def test_failed_group_never_exposes_previous_iteration_to_outer_downstream(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="failed-group-output",
        nodes=[
            _group(
                [{"id": "sink", "prompt": "sink", "retry": {"max_attempts": 1}}],
                maximum=2,
            ),
            {"id": "downstream", "prompt": "$group.output", "depends_on": ["group"]},
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="failed-group-output")

    def output(context, _rendered):
        if context.node.id == "downstream":
            raise AssertionError("failed loop group output reached downstream")
        if "iterations/0002" in context.effective_attempt_directory.as_posix():
            return NodeExecutionResult(
                "failed",
                error_code="validation",
                error_message="second iteration failed",
                metadata={"archon_terminal_failure": True},
            )
        return "first iteration"

    executor = OutputExecutor(output)
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["prompt"] = executor

    result = scheduler.advance_all([run_id])[run_id]

    assert result["status"] == "failed"
    assert result["nodes"]["downstream"]["state"] == "pending"
    assert result["nodes"]["group"]["loop_group"]["previous_outputs"]
    assert "group" not in scheduler._output_values(
        result, store.run_directory(run_id), node_ids=("group",)
    )
