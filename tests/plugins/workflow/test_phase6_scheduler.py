from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import threading

import pytest

from agent.plugin_agent import PluginAgentRunResult
from hermes_cli.runtime_provider import classify_execution_runtime
from hermes_cli.workflow_model_resolution import parse_workflow_model_config
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.entitlement import AIEntitlementResolution
from plugins.workflow.executors.base import NodeExecutionResult
from plugins.workflow.executors.bash import BashExecutor
from plugins.workflow.executors.script import ScriptExecutor
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
from plugins.workflow.sessions import NodeSessionKey
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


def _compile(
    tmp_path: Path,
    workflow_writer,
    *,
    name: str,
    nodes,
    normalizer_version: int = 6,
    **options,
):
    root = tmp_path / name
    commands = root / "commands"
    commands.mkdir(parents=True)
    (commands / "nested-command.md").write_text("nested command", encoding="utf-8")
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "nested-script.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['ARTIFACTS_DIR']).joinpath('script-child.txt').write_text('script')\n"
        "print('script')\n",
        encoding="utf-8",
    )
    (scripts / "predecessor-script.py").write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "data = json.loads(Path(os.environ['HERMES_WORKFLOW_PREDECESSORS_FILE']).read_text())\n"
        "print(','.join(sorted(data)))\n",
        encoding="utf-8",
    )
    (scripts / "artifact-free-path.py").write_text(
        "import os\n"
        "print(os.environ['ARTIFACTS_DIR'])\n",
        encoding="utf-8",
    )
    (scripts / "artifact-free-escape.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "run = Path(os.environ['HERMES_WORKFLOW_RUN_DIR'])\n"
        "cwd = Path.cwd()\n"
        "artifacts = Path(os.environ['ARTIFACTS_DIR'])\n"
        "for root, name in ((run, 'env-hidden'), (cwd, 'relative-hidden')):\n"
        "    root.joinpath('artifacts').mkdir(parents=True, exist_ok=True)\n"
        "    root.joinpath('artifacts', name).write_text(str(root))\n"
        "print(run)\n"
        "print(cwd)\n"
        "print(artifacts)\n",
        encoding="utf-8",
    )
    (scripts / "publishing-context.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "artifacts = Path(os.environ['ARTIFACTS_DIR'])\n"
        "artifacts.joinpath('context.txt').write_text('\\n'.join((\n"
        "    os.environ['HERMES_WORKFLOW_RUN_DIR'],\n"
        "    str(Path.cwd()),\n"
        "    str(artifacts),\n"
        ")))\n"
        "print('ok <promise>DONE</promise>')\n",
        encoding="utf-8",
    )
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
        normalizer_version=normalizer_version,
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


def _group(
    body,
    *,
    maximum: int = 1,
    depends_on=(),
    fresh_context: bool = False,
    **options,
):
    return {
        "id": "group",
        "depends_on": list(depends_on),
        "loop_group": {
            "until": "DONE",
            "max_iterations": maximum,
            "nodes": body,
            "fresh_context": fresh_context,
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


class RecordingProcessExecutor:
    def __init__(self, executor) -> None:
        self.executor = executor
        self.contexts = []

    def execute(self, context):
        self.contexts.append(context)
        return self.executor.execute(context)


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


class PersistentRunner:
    def __init__(self) -> None:
        self.requests = []

    def run(self, request, **_kwargs):
        self.requests.append(request)
        return PluginAgentRunResult(
            final_response=f"iteration-{len(self.requests)}",
            session_id=f"session-{len(self.requests)}",
            provider=request.provider or "openrouter",
            model=request.model or "openai/gpt-5.4",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={
                "provider_attempts": 1,
                "model_calls": 1,
                "intended_authority_digest": request.intended_authority_digest,
                "model_visible_prefix_digest": "9" * 64,
            },
        )


def test_pre_v6_top_level_ready_nodes_keep_lexical_id_order(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="legacy-order",
        nodes=[
            {"id": "z-first", "bash": "true"},
            {"id": "a-second", "bash": "true"},
        ],
        normalizer_version=5,
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="legacy-order")
    executor = SucceedingExecutor()
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["bash"] = executor

    scheduler.advance_all([run_id])

    assert [context.node.id for context in executor.contexts] == [
        "a-second",
        "z-first",
    ]


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


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_real_bash_and_named_script_publish_only_under_scoped_artifact_roots(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="scoped-publications",
        nodes=[
            _group([
                {
                    "id": "bash",
                    "bash": (
                        'printf bash > "$ARTIFACTS_DIR/bash-child.txt"; '
                        "printf bash"
                    ),
                },
                {
                    "id": "script",
                    "script": "nested-script",
                    "runtime": "uv",
                },
            ])
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=2)
    run_id = _admit(store, compilation, key="scoped-publications")

    result = RunScheduler(store, max_parallel_nodes=2).advance_all([run_id])[run_id]

    run_directory = store.run_directory(run_id)
    publication_root = (
        run_directory / "artifacts" / "loop-groups" / "group" / "iterations" / "0001"
    )
    assert {
        child["state"]
        for child in result["nodes"]["group"]["loop_group"]["body"].values()
    } == {"succeeded"}
    assert (publication_root / "bash" / "bash-child.txt").read_text() == "bash"
    assert (publication_root / "script" / "script-child.txt").read_text() == "script"
    assert not (run_directory / "artifacts" / "bash-child.txt").exists()
    assert not (run_directory / "artifacts" / "script-child.txt").exists()


def test_artifact_free_scoped_script_receives_zero_runtime_ceiling(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="artifact-free-context",
        nodes=[
            _group([
                {
                    "id": "reduce",
                    "script": "nested-script",
                    "runtime": "uv",
                    "artifacts": False,
                }
            ])
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="artifact-free-context")
    executor = SucceedingExecutor()
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["script"] = executor

    scheduler.advance_all([run_id])

    assert len(executor.contexts) == 1
    assert executor.contexts[0].max_artifact_bytes == 0


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
@pytest.mark.parametrize(
    ("surface", "scoped"),
    (
        ("bash", False),
        ("bash", True),
        ("inline-script", False),
        ("inline-script", True),
        ("named-script", False),
    ),
)
def test_v6_artifact_free_process_uses_one_attempt_private_rendered_workspace(
    tmp_path, workflow_writer, surface, scoped
) -> None:
    if surface == "bash":
        value = (
            "rendered='$ARTIFACTS_DIR'; "
            "exported=$(printenv ARTIFACTS_DIR); "
            'test "$rendered" = "$exported"; printf \'%s\' "$exported"'
        )
        node = {"id": "artifact-free", "bash": value, "artifacts": False}
        executor = RecordingProcessExecutor(BashExecutor())
        executor_name = "bash"
    else:
        value = (
            "artifact-free-path"
            if surface == "named-script"
            else (
                "import os\n"
                "rendered = '$ARTIFACTS_DIR'\n"
                "assert rendered == os.environ['ARTIFACTS_DIR']\n"
                "print(rendered)\n"
            )
        )
        node = {
            "id": "artifact-free",
            "script": value,
            "runtime": "uv",
            "artifacts": False,
        }
        executor = RecordingProcessExecutor(ScriptExecutor())
        executor_name = "script"
    if scoped:
        if executor_name == "bash":
            value = str(node[executor_name]) + (
                "\nprintf '\\n<promise>DONE</promise>'"
            )
        else:
            value = str(node[executor_name]).replace(
                "print(rendered)\n",
                "print(rendered)\nprint('<promise>DONE</promise>')\n",
            )
        node[executor_name] = value
        nodes = [_group([node])]
    else:
        nodes = [node]
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name=f"artifact-free-path-{surface}-{scoped}",
        nodes=nodes,
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key=f"artifact-free-path-{surface}-{scoped}")
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors[executor_name] = executor

    result = scheduler.advance_all([run_id])[run_id]

    [execution] = executor.contexts
    private_workspace = execution.effective_attempt_directory / "artifacts"
    assert execution.effective_publication_directory == private_workspace
    assert isinstance(execution.variable_context, VariableContext)
    assert execution.variable_context.artifacts_dir == private_workspace
    assert (
        execution.effective_attempt_directory / "stdout.txt"
    ).read_text(encoding="utf-8").splitlines()[0] == str(private_workspace)
    state = (
        result["nodes"]["group"]["loop_group"]["body"]["artifact-free"]
        if scoped
        else result["nodes"]["artifact-free"]
    )
    assert state["state"] == "succeeded"


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
@pytest.mark.parametrize(
    ("surface", "scoped"),
    (
        ("bash", False),
        ("bash", True),
        ("inline-script", False),
        ("inline-script", True),
        ("named-script", False),
        ("named-script", True),
    ),
)
def test_v6_artifact_free_process_watches_run_env_and_relative_cwd(
    tmp_path, workflow_writer, surface, scoped
) -> None:
    if surface == "bash":
        value = (
            'mkdir -p "$HERMES_WORKFLOW_RUN_DIR/artifacts" artifacts; '
            "printf '%s' \"$HERMES_WORKFLOW_RUN_DIR\" > "
            '"$HERMES_WORKFLOW_RUN_DIR/artifacts/env-hidden"; '
            "printf '%s' \"$PWD\" > artifacts/relative-hidden; "
            "printf '%s\\n%s\\n%s\\n' \"$HERMES_WORKFLOW_RUN_DIR\" "
            '"$PWD" "$ARTIFACTS_DIR"'
        )
        node = {"id": "artifact-free", "bash": value, "artifacts": False}
        executor = RecordingProcessExecutor(BashExecutor())
        executor_name = "bash"
    else:
        value = (
            "artifact-free-escape"
            if surface == "named-script"
            else (
                "import os\n"
                "from pathlib import Path\n"
                "run = Path(os.environ['HERMES_WORKFLOW_RUN_DIR'])\n"
                "cwd = Path.cwd()\n"
                "artifacts = Path(os.environ['ARTIFACTS_DIR'])\n"
                "for root, name in ((run, 'env-hidden'), (cwd, 'relative-hidden')):\n"
                "    root.joinpath('artifacts').mkdir(parents=True, exist_ok=True)\n"
                "    root.joinpath('artifacts', name).write_text(str(root))\n"
                "print(run)\n"
                "print(cwd)\n"
                "print(artifacts)\n"
            )
        )
        node = {
            "id": "artifact-free",
            "script": value,
            "runtime": "uv",
            "artifacts": False,
        }
        executor = RecordingProcessExecutor(ScriptExecutor())
        executor_name = "script"
    nodes = [_group([node])] if scoped else [node]
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name=f"artifact-free-process-view-{surface}-{scoped}",
        nodes=nodes,
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(
        store,
        compilation,
        key=f"artifact-free-process-view-{surface}-{scoped}",
    )
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors[executor_name] = executor

    result = scheduler.advance_all([run_id])[run_id]

    [execution] = executor.contexts
    private_run = execution.effective_attempt_directory
    private_artifacts = private_run / "artifacts"
    state = (
        result["nodes"]["group"]["loop_group"]["body"]["artifact-free"]
        if scoped
        else result["nodes"]["artifact-free"]
    )
    assert state["state"] != "succeeded", state
    assert state["attempts"][-1]["error_code"] == "artifact_limit"
    assert (private_artifacts / "env-hidden").read_text() == str(private_run)
    assert (private_artifacts / "relative-hidden").read_text() == str(private_run)
    assert (
        private_run / "stdout.txt"
    ).read_text().splitlines() == [
        str(private_run),
        str(private_run),
        str(private_artifacts),
    ]
    public_root = store.run_directory(run_id) / "artifacts"
    assert not public_root.exists() or not list(public_root.rglob("*hidden"))


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
@pytest.mark.parametrize(
    ("surface", "scoped", "normalizer_version"),
    (
        ("bash", False, 6),
        ("bash", True, 6),
        ("inline-script", False, 6),
        ("inline-script", True, 6),
        ("named-script", False, 6),
        ("named-script", True, 6),
        ("bash", False, 5),
        ("named-script", False, 5),
    ),
)
def test_publishing_process_keeps_real_run_cwd_env_and_exact_publication(
    tmp_path, workflow_writer, surface, scoped, normalizer_version
) -> None:
    if surface == "bash":
        value = (
            "printf '%s\\n%s\\n%s' \"$HERMES_WORKFLOW_RUN_DIR\" "
            '"$PWD" "$ARTIFACTS_DIR" > "$ARTIFACTS_DIR/context.txt"; '
            "printf 'ok <promise>DONE</promise>'"
        )
        node = {"id": "publisher", "bash": value}
    else:
        value = (
            "publishing-context"
            if surface == "named-script"
            else (
                "import os\n"
                "from pathlib import Path\n"
                "artifacts = Path(os.environ['ARTIFACTS_DIR'])\n"
                "artifacts.joinpath('context.txt').write_text('\\n'.join((\n"
                "    os.environ['HERMES_WORKFLOW_RUN_DIR'],\n"
                "    str(Path.cwd()),\n"
                "    str(artifacts),\n"
                ")))\n"
                "print('ok <promise>DONE</promise>')\n"
            )
        )
        node = {"id": "publisher", "script": value, "runtime": "uv"}
    nodes = [_group([node])] if scoped else [node]
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name=f"publishing-process-view-{surface}-{scoped}-v{normalizer_version}",
        nodes=nodes,
        normalizer_version=normalizer_version,
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(
        store,
        compilation,
        key=f"publishing-process-view-{surface}-{scoped}-v{normalizer_version}",
    )

    result = RunScheduler(store, max_parallel_nodes=1).advance_all([run_id])[run_id]

    run_directory = store.run_directory(run_id)
    publication = (
        run_directory / "artifacts/loop-groups/group/iterations/0001/publisher"
        if scoped
        else run_directory / "artifacts"
    )
    state = (
        result["nodes"]["group"]["loop_group"]["body"]["publisher"]
        if scoped
        else result["nodes"]["publisher"]
    )
    assert state["state"] == "succeeded", state
    assert (publication / "context.txt").read_text().splitlines() == [
        str(run_directory),
        str(run_directory),
        str(publication),
    ]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
@pytest.mark.parametrize(
    ("surface", "scoped"),
    (("bash", False), ("script", True)),
)
def test_v6_artifact_free_retry_keeps_failed_residue_in_its_original_attempt(
    tmp_path, workflow_writer, surface, scoped
) -> None:
    marker = tmp_path / f"retry-{surface}-{scoped}.marker"
    if surface == "bash":
        command = (
            f"if [ ! -e {str(marker)!r} ]; then "
            f": > {str(marker)!r}; "
            "printf residue > \"$ARTIFACTS_DIR/forbidden.txt\"; fi; "
            "printf 'ok <promise>DONE</promise>'"
        )
        node = {"id": "artifact-free", "bash": command, "artifacts": False}
        executor = RecordingProcessExecutor(BashExecutor())
    else:
        command = (
            "import os\n"
            "from pathlib import Path\n"
            f"marker = Path({str(marker)!r})\n"
            "if not marker.exists():\n"
            "    marker.touch()\n"
            "    Path(os.environ['ARTIFACTS_DIR']).joinpath('forbidden.txt').write_text('residue')\n"
            "print('ok <promise>DONE</promise>')\n"
        )
        node = {
            "id": "artifact-free",
            "script": command,
            "runtime": "uv",
            "artifacts": False,
        }
        executor = RecordingProcessExecutor(ScriptExecutor())
    node["retry"] = {"max_attempts": 2, "on_error": "all", "delay_ms": 1000}
    if scoped:
        group = _group([node])
        group["loop_group"]["signal_completes"] = True
        nodes = [group]
    else:
        nodes = [node]
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name=f"artifact-free-retry-{surface}-{scoped}",
        nodes=nodes,
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key=f"artifact-free-retry-{surface}-{scoped}")

    class RetryableArtifactExecutor:
        def execute(self, context):
            result = executor.execute(context)
            if result.error_code != "artifact_limit":
                return result
            return NodeExecutionResult(
                "failed",
                error_code="provider_timeout",
                error_message=result.error_message,
                metadata={
                    "known_no_effect": True,
                    "provider_attempts": 0,
                    "provider_attempts_exact": True,
                },
            )

    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    scheduler = RunScheduler(
        store,
        max_parallel_nodes=1,
        utcnow=lambda: now,
        jitter=lambda: 0.5,
    )
    scheduler.executors[surface] = RetryableArtifactExecutor()

    waiting = scheduler.advance_all([run_id])[run_id]
    waiting_target = (
        waiting["nodes"]["group"]["loop_group"]["body"]["artifact-free"]
        if scoped
        else waiting["nodes"]["artifact-free"]
    )
    assert waiting_target["state"] == "waiting_retry", (
        waiting.get("last_error"),
        waiting_target.get("retry_consumed"),
        waiting_target["attempts"][-1].get("metadata"),
    )
    now += timedelta(seconds=1)
    result = scheduler.advance_all([run_id])[run_id]

    target_state = (
        result["nodes"]["group"]["loop_group"]["body"]["artifact-free"]
        if scoped
        else result["nodes"]["artifact-free"]
    )
    assert target_state["state"] == "succeeded", target_state
    assert len(executor.contexts) == 2
    first, second = executor.contexts
    assert first.attempt_id != second.attempt_id
    assert first.effective_publication_directory == (
        first.effective_attempt_directory / "artifacts"
    )
    assert second.effective_publication_directory == (
        second.effective_attempt_directory / "artifacts"
    )
    assert first.effective_publication_directory != second.effective_publication_directory
    assert (first.effective_publication_directory / "forbidden.txt").read_text() == (
        "residue"
    )
    assert not (second.effective_publication_directory / "forbidden.txt").exists()
    assert not list(
        (store.run_directory(run_id) / "artifacts").rglob("forbidden.txt")
    )


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
@pytest.mark.parametrize("surface", ("bash", "script"))
def test_top_level_artifact_free_process_ignores_concurrent_child_publication(
    tmp_path, workflow_writer, surface
) -> None:
    ready = tmp_path / f"concurrent-{surface}.ready"
    published = tmp_path / f"concurrent-{surface}.published"
    if surface == "bash":
        node = {
            "id": "artifact-free",
            "bash": (
                f": > {str(ready)!r}; "
                f"while [ ! -e {str(published)!r} ]; do sleep 0.01; done; "
                "printf ok"
            ),
            "artifacts": False,
        }
        executor = RecordingProcessExecutor(BashExecutor())
    else:
        node = {
            "id": "artifact-free",
            "script": (
                "import time\n"
                "from pathlib import Path\n"
                f"ready = Path({str(ready)!r})\n"
                f"published = Path({str(published)!r})\n"
                "ready.touch()\n"
                "while not published.exists():\n"
                "    time.sleep(0.01)\n"
                "print('ok')\n"
            ),
            "runtime": "uv",
            "artifacts": False,
        }
        executor = RecordingProcessExecutor(ScriptExecutor())
    publisher = {
        "id": "publisher",
        "bash": (
            f"while [ ! -e {str(ready)!r} ]; do sleep 0.01; done; "
            'printf published > "$ARTIFACTS_DIR/result.txt"; '
            f": > {str(published)!r}; "
            "printf published"
        ),
    }
    group = _group([publisher])
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name=f"artifact-free-concurrent-{surface}",
        nodes=[node, group],
    )
    store = RunStore(tmp_path / "home", max_total_workers=2)
    run_id = _admit(store, compilation, key=f"artifact-free-concurrent-{surface}")
    scheduler = RunScheduler(store, max_parallel_nodes=2)
    scheduler.executors[surface] = executor

    result = scheduler.advance_all([run_id])[run_id]

    run_directory = store.run_directory(run_id)
    [execution] = [
        context for context in executor.contexts if context.node.id == "artifact-free"
    ]
    assert result["nodes"]["artifact-free"]["state"] == "succeeded", result["nodes"]
    publisher_state = result["nodes"]["group"]["loop_group"]["body"]["publisher"]
    assert publisher_state["state"] == "succeeded", publisher_state
    assert execution.effective_publication_directory == (
        execution.effective_attempt_directory / "artifacts"
    )
    publication = (
        run_directory
        / "artifacts/loop-groups/group/iterations/0001/publisher/result.txt"
    )
    assert publication.read_text() == "published"


@pytest.mark.parametrize(
    ("case", "command"),
    (
        ("file", ': > "$ARTIFACTS_DIR/entry"'),
        ("directory", 'mkdir "$ARTIFACTS_DIR/entry"'),
        ("create-remove", ': > "$ARTIFACTS_DIR/entry"; rm "$ARTIFACTS_DIR/entry"'),
        ("symlink", 'ln -s nowhere "$ARTIFACTS_DIR/entry"'),
        (
            "hardlink",
            ': > "$HERMES_WORKFLOW_RUN_DIR/hardlink-source"; '
            'ln "$HERMES_WORKFLOW_RUN_DIR/hardlink-source" "$ARTIFACTS_DIR/entry"',
        ),
        ("fifo", 'mkfifo "$ARTIFACTS_DIR/entry"'),
        (
            "root-replacement",
            'mv "$ARTIFACTS_DIR" "$ARTIFACTS_DIR-old"; mkdir "$ARTIFACTS_DIR"',
        ),
    ),
)
def test_attempt_private_artifact_workspace_keeps_filesystem_checks_fail_closed(
    tmp_path, workflow_writer, case, command
) -> None:
    node = {
        "id": "artifact-free",
        "bash": f"{command}; printf ok",
        "artifacts": False,
        "retry": {"max_attempts": 1},
    }
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name=f"artifact-free-filesystem-{case}",
        nodes=[node],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key=f"artifact-free-filesystem-{case}")
    executor = RecordingProcessExecutor(BashExecutor())
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["bash"] = executor

    result = scheduler.advance_all([run_id])[run_id]

    [execution] = executor.contexts
    assert execution.effective_publication_directory == (
        execution.effective_attempt_directory / "artifacts"
    )
    state = result["nodes"]["artifact-free"]
    assert state["state"] != "succeeded"
    assert state["attempts"][-1]["error_code"] == "artifact_limit"
    public_root = store.run_directory(run_id) / "artifacts"
    assert not public_root.exists() or not any(public_root.iterdir())


def test_scoped_script_receives_its_semantic_structured_output_contract(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="scoped-structured-script",
        nodes=[
            _group([
                {
                    "id": "reduce",
                    "script": "console.log(JSON.stringify({present: 'yes'}))",
                    "runtime": "bun",
                    "output_format": {
                        "type": "object",
                        "properties": {"present": {"type": "string"}},
                        "required": ["present"],
                        "additionalProperties": False,
                    },
                }
            ])
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="scoped-structured-script")
    executor = SucceedingExecutor()
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["script"] = executor

    scheduler.advance_all([run_id])

    assert len(executor.contexts) == 1
    assert executor.contexts[0].structured_output == (
        compilation.package.language.structured_outputs["group/reduce"]
    )
    assert executor.contexts[0].structured_output_decision is None


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is not installed")
def test_zero_write_flag_skips_approval_and_tool_node_but_runs_terminal_reducer(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="conditional-write-skip",
        nodes=[
            _group([
                {
                    "id": "prepare",
                    "script": "console.log(JSON.stringify({should_write: 0}))",
                    "runtime": "bun",
                    "artifacts": False,
                    "output_format": {
                        "type": "object",
                        "properties": {"should_write": {"const": 0}},
                        "required": ["should_write"],
                        "additionalProperties": False,
                    },
                },
                {
                    "id": "approve",
                    "depends_on": ["prepare"],
                    "when": "$prepare.output.should_write == 1",
                    "approval": {"message": "must not pause"},
                },
                {
                    "id": "write",
                    "depends_on": ["prepare", "approve"],
                    "when": "$prepare.output.should_write == 1",
                    "prompt": "must not execute",
                },
                {
                    "id": "finish",
                    "depends_on": ["prepare", "approve", "write"],
                    "trigger_rule": "none_failed_min_one_success",
                    "script": (
                        "const plan = $prepare.output; "
                        "console.log(JSON.stringify({should_write: plan.should_write, "
                        "completion: '<promise>DONE</promise>'}))"
                    ),
                    "runtime": "bun",
                    "artifacts": False,
                    "output_format": {
                        "type": "object",
                        "properties": {
                            "should_write": {"const": 0},
                            "completion": {
                                "enum": ["", "<promise>DONE</promise>"]
                            },
                        },
                        "required": ["should_write", "completion"],
                        "additionalProperties": False,
                    },
                },
            ])
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="conditional-write-skip")

    class ForbiddenExecutor:
        def execute(self, _context):
            raise AssertionError("a guarded approval/write node executed")

    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["approval"] = ForbiddenExecutor()
    scheduler.executors["prompt"] = ForbiddenExecutor()
    observed = []

    class RecordingScriptExecutor:
        def execute(self, context):
            if _body_id(context) == "finish":
                observed.append(context.predecessor_results)
            return ScriptExecutor().execute(context)

    scheduler.executors["script"] = RecordingScriptExecutor()

    result = scheduler.advance_all([run_id])[run_id]

    body = result["nodes"]["group"]["loop_group"]["body"]
    assert body["prepare"]["state"] == "succeeded"
    assert body["approve"]["state"] == "skipped"
    assert body["write"]["state"] == "skipped"
    assert body["finish"]["state"] == "succeeded"
    assert result["nodes"]["group"]["state"] == "succeeded"
    assert observed[0]["prepare"]["state"] == "succeeded"
    assert observed[0]["prepare"]["output"] == {"should_write": 0}
    assert observed[0]["approve"] == {"state": "skipped"}
    assert observed[0]["write"] == {"state": "skipped"}


def test_scoped_shared_context_uses_only_original_body_predecessor_evidence(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="scoped-predecessor",
        nodes=[
            {"id": "outer", "prompt": "outer"},
            _group(
                [
                    {"id": "producer", "prompt": "producer"},
                    {
                        "id": "consumer",
                        "prompt": "consumer",
                        "depends_on": ["producer"],
                        "context": "shared",
                    },
                ],
                depends_on=("outer",),
            ),
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="scoped-predecessor")
    observed = []

    class EvidenceExecutor:
        def execute(self, context):
            node_id = _body_id(context)
            identity = {
                "intended_authority_digest": "a" * 64,
                "model_visible_prefix_digest": "b" * 64,
                "shared_context_compatibility_digest": "c" * 64,
            }
            if node_id == "consumer":
                observed.append(context.predecessor_results)
                return NodeExecutionResult("succeeded")
            return NodeExecutionResult(
                "succeeded",
                metadata={
                    "session_id": f"{node_id}-session",
                    "cache_fingerprint": "d" * 64,
                    **identity,
                },
            )

    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["prompt"] = EvidenceExecutor()

    scheduler.advance_all([run_id])

    assert len(observed) == 1
    assert set(observed[0]) == {"producer"}
    assert observed[0]["producer"]["session_id"] == "producer-session"


def test_scoped_typed_publication_is_canonical_and_recovers(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="scoped-typed-publication",
        nodes=[
            _group([
                {
                    "id": "typed",
                    "bash": "printf durable-report",
                    "output_type": "Report",
                }
            ])
        ],
    )
    home = tmp_path / "home"
    store = RunStore(home, max_total_workers=1)
    run_id = _admit(store, compilation, key="scoped-typed-publication")

    result = RunScheduler(store, max_parallel_nodes=1).advance_all([run_id])[run_id]

    [publication] = [
        artifact for artifact in result["artifacts"] if "publication_id" in artifact
    ]
    bundle = store.run_directory(run_id) / "publications" / publication["publication_id"]
    metadata = json.loads((bundle / "metadata.json").read_text())
    assert (bundle / "content.md").read_text() == "durable-report"
    assert publication["node_id"] == "group/typed"
    assert metadata["node_id"] == "group/typed"
    assert metadata["output_type"] == "Report"
    recovered = RunStore(home).load_run(run_id)
    assert next(
        artifact["publication_id"]
        for artifact in recovered["artifacts"]
        if "publication_id" in artifact
    ) == publication["publication_id"]


def test_scoped_typed_publications_recover_across_iteration_rollover(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="scoped-typed-publication-rollover",
        nodes=[
            _group(
                [
                    {
                        "id": "typed",
                        "bash": "printf durable-report",
                        "output_type": "Report",
                    }
                ],
                maximum=2,
            )
        ],
    )
    home = tmp_path / "home"
    store = RunStore(home, max_total_workers=1)
    run_id = _admit(store, compilation, key="scoped-typed-publication-rollover")

    first = RunScheduler(store, max_parallel_nodes=1).advance(run_id, max_nodes=1)

    controller = first["nodes"]["group"]["loop_group"]
    assert controller["iteration"] == 2
    assert controller["body"]["typed"]["attempts"] == []
    events = [
        json.loads(line)
        for line in (store.run_directory(run_id) / "events.jsonl").read_text().splitlines()
    ]
    assert any(
        event["event_type"] == "loop_group_iteration_committed"
        and event["payload"]["completed_iteration"] == 1
        for event in events
    )
    [first_publication] = [
        artifact for artifact in first["artifacts"] if "publication_id" in artifact
    ]
    assert first_publication["loop_group_scope"]["iteration"] == 1

    reopened_store = RunStore(home, max_total_workers=1)
    reopened = reopened_store.load_run(run_id)
    assert reopened["nodes"]["group"]["loop_group"]["iteration"] == 2

    RunScheduler(reopened_store, max_parallel_nodes=1).advance(run_id, max_nodes=1)
    recovered = RunStore(home).load_run(run_id)
    publications = [
        artifact
        for artifact in recovered["artifacts"]
        if "publication_id" in artifact
    ]
    assert len(publications) == 2
    assert [
        publication["loop_group_scope"]["iteration"]
        for publication in publications
    ] == [1, 2]
    assert all(publication["node_id"] == "group/typed" for publication in publications)
    assert [
        RunStore(home).lookup_publication(
            run_id, publication["publication_id"]
        ).content
        for publication in publications
    ] == [b"durable-report", b"durable-report"]


def test_persistent_ai_child_reuses_scoped_session_across_iterations(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="scoped-persistent-session",
        nodes=[
            _group(
                [
                    {
                        "id": "ask",
                        "prompt": "continue",
                        "persist_session": True,
                    }
                ],
                maximum=2,
                fresh_context=False,
                provider="openrouter",
                model="openai/gpt-5.4",
            )
        ],
        persist_sessions=True,
        provider="openrouter",
        model="openai/gpt-5.4",
    )
    home = tmp_path / "home"
    store = RunStore(home, max_total_workers=1)
    run_id = _admit(store, compilation, key="scoped-persistent-session")
    runner = PersistentRunner()
    scheduler = RunScheduler(store, agent_runner=runner, max_parallel_nodes=1)

    result = scheduler.advance_all([run_id])[run_id]

    assert result["nodes"]["group"]["loop_group"]["iteration"] == 2
    assert [request.context_mode for request in runner.requests] == ["fresh", "shared"]
    assert runner.requests[1].session_id == "session-1"
    record = scheduler.session_registry.get(
        NodeSessionKey(
            "scoped-persistent-session",
            "group/ask",
            "local",
            "openrouter",
            "default",
        )
    )
    assert record is not None
    assert record.session_id == "session-2"
    assert record.generation == 2
    assert store.pending_session_registry_update(run_id) is None


def test_v6_ai_child_max_turns_reaches_real_runner_and_accounting(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="scoped-turn-cap",
        nodes=[
            _group(
                [{"id": "ask", "prompt": "continue", "maxTurns": 2}],
                maximum=1,
                provider="openrouter",
                model="openai/gpt-5.4",
            )
        ],
        provider="openrouter",
        model="openai/gpt-5.4",
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="scoped-turn-cap")
    runner = PersistentRunner()

    result = RunScheduler(
        store, agent_runner=runner, max_parallel_nodes=1
    ).advance_all([run_id])[run_id]

    assert [request.max_iterations for request in runner.requests] == [2]
    assert [request.strict_iteration_limit for request in runner.requests] == [True]
    state = result["nodes"]["group"]["loop_group"]["body"]["ask"]
    assert state["iteration_consumed"] == 1
    assert state["remaining_iterations"] == 1


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


def test_group_until_bash_executes_current_outer_and_previous_scopes(
    tmp_path, workflow_writer
) -> None:
    group = _group(
        [{"id": "sink", "prompt": "sink"}],
        maximum=2,
        depends_on=("outer",),
    )
    group["loop_group"]["until_bash"] = (
        "test \"$sink.output|$outer.output|$LOOP_PREV.sink.output\" "
        "= \"sink-2|outer|sink-1\""
    )
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="scoped-until-bash",
        nodes=[
            {"id": "outer", "prompt": "outer"},
            group,
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key="scoped-until-bash")

    def output(context, _rendered):
        if context.node.id == "outer":
            return "outer"
        iteration = (
            2
            if "iterations/0002" in context.effective_attempt_directory.as_posix()
            else 1
        )
        return f"sink-{iteration}"

    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["prompt"] = OutputExecutor(output)

    result = scheduler.advance_all([run_id])[run_id]

    assert result["status"] == "succeeded"
    assert result["nodes"]["group"]["loop_group"]["iteration"] == 2


def test_scoped_renderer_rejects_malformed_previous_reference_prefix() -> None:
    renderer = substitution_renderer(
        VariableContext(
            previous_body_outputs={"producer": None},
            normalizer_version=6,
        ),
        direct_dependencies=(),
    )

    with pytest.raises(WorkflowOutputReferenceError) as raised:
        renderer.render_prompt("$LOOP_PREV.producer.outputx")

    assert raised.value.code == "output_reference_path_unsupported"


@pytest.mark.parametrize(
    "template",
    (
        "printf ok # $LOOP_PREV.producer.outputx",
        r"printf '%s' \$LOOP_PREV.producer.outputx",
    ),
    ids=("comment", "escaped-literal"),
)
def test_scoped_bash_renderer_ignores_malformed_previous_text_outside_references(
    tmp_path, template
) -> None:
    renderer = substitution_renderer(
        VariableContext(
            previous_body_outputs={"producer": None},
            normalizer_version=6,
        ),
        direct_dependencies=(),
    )

    rendered = renderer.render_bash(
        template,
        spill_directory=tmp_path / "spills",
        secure_v3=True,
    )

    try:
        assert rendered.command == template
    finally:
        rendered.close()


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
@pytest.mark.parametrize("script", ("inline", "named"))
@pytest.mark.parametrize("body_dependency", (False, True), ids=("outer-only", "body-outer"))
def test_scoped_script_predecessor_evidence_matches_runtime_dependencies(
    tmp_path, workflow_writer, script, body_dependency
) -> None:
    inline = (
        "import json, os\n"
        "from pathlib import Path\n"
        "data = json.loads(Path(os.environ['HERMES_WORKFLOW_PREDECESSORS_FILE']).read_text())\n"
        "print(','.join(sorted(data)))\n"
    )
    body = []
    if body_dependency:
        body.append({"id": "producer", "prompt": "producer"})
    body.append({
        "id": "reduce",
        "depends_on": ["producer"] if body_dependency else [],
        "script": inline if script == "inline" else "predecessor-script",
        "runtime": "uv",
    })
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name=f"scoped-script-{script}-{body_dependency}",
        nodes=[
            {"id": "outer", "prompt": "outer"},
            _group(body, depends_on=("outer",)),
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(
        store,
        compilation,
        key=f"scoped-script-{script}-{body_dependency}",
    )
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["prompt"] = OutputExecutor(
        lambda context, _rendered: context.node.id.rsplit("/", 1)[-1]
    )

    result = scheduler.advance_all([run_id])[run_id]

    script_state = result["nodes"]["group"]["loop_group"]["body"]["reduce"]
    assert script_state["state"] == "succeeded"
    assert not list(store.run_directory(run_id).rglob("predecessors.json"))


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
@pytest.mark.parametrize("script", ("inline", "named"))
def test_scoped_script_outer_evidence_wins_undeclared_body_id_collision(
    tmp_path, workflow_writer, script
) -> None:
    inline = (
        "import json, os\n"
        "from pathlib import Path\n"
        "data = json.loads(Path(os.environ['HERMES_WORKFLOW_PREDECESSORS_FILE']).read_text())\n"
        "print(data['shared']['output'])\n"
    )
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name=f"scoped-script-collision-{script}",
        nodes=[
            {"id": "shared", "prompt": "outer"},
            _group(
                [
                    {
                        "id": "shared",
                        "when": "$LOOP_PREV.shared.output == 'run'",
                        "prompt": "body",
                    },
                    {
                        "id": "reduce",
                        "script": inline if script == "inline" else "predecessor-script",
                        "runtime": "uv",
                    },
                    {
                        "id": "finish",
                        "depends_on": ["shared", "reduce"],
                        "trigger_rule": "none_failed_min_one_success",
                        "prompt": "finish",
                    },
                ],
                depends_on=("shared",),
            ),
        ],
    )
    store = RunStore(tmp_path / "home", max_total_workers=1)
    run_id = _admit(store, compilation, key=f"scoped-script-collision-{script}")
    scheduler = RunScheduler(store, max_parallel_nodes=1)
    scheduler.executors["prompt"] = OutputExecutor(
        lambda context, _rendered: (
            "outer" if context.node.id == "shared" else "finish"
        )
    )

    result = scheduler.advance_all([run_id])[run_id]

    script_state = result["nodes"]["group"]["loop_group"]["body"]["reduce"]
    assert script_state["state"] == "succeeded"
    assert not list(store.run_directory(run_id).rglob("predecessors.json"))


def test_previous_output_body_conditions_replay_from_authenticated_recovery_state(
    tmp_path, workflow_writer
) -> None:
    compilation = _compile(
        tmp_path,
        workflow_writer,
        name="previous-condition-recovery",
        nodes=[
            _group(
                [
                    {"id": "producer", "prompt": "producer"},
                    {
                        "id": "first-only",
                        "depends_on": ["producer"],
                        "when": "$LOOP_PREV.producer.output == ''",
                        "prompt": "first-only",
                    },
                    {
                        "id": "later-only",
                        "depends_on": ["producer"],
                        "when": "$LOOP_PREV.producer.output == 'producer-1'",
                        "prompt": "later-only",
                    },
                    {
                        "id": "finish",
                        "depends_on": ["producer", "first-only", "later-only"],
                        "trigger_rule": "none_failed_min_one_success",
                        "prompt": "finish",
                    },
                ],
                maximum=2,
            )
        ],
    )
    home = tmp_path / "home"
    store = RunStore(home, max_total_workers=1)
    run_id = _admit(store, compilation, key="previous-condition-recovery")

    def first_output(context, _rendered):
        node_id = _body_id(context)
        if node_id == "producer":
            return "producer-1"
        if node_id == "finish":
            return "continue"
        return node_id

    first_executor = OutputExecutor(first_output)
    first_scheduler = RunScheduler(store, max_parallel_nodes=1)
    first_scheduler.executors["prompt"] = first_executor

    before_restart = first_scheduler.advance(run_id, max_nodes=3)

    assert before_restart["nodes"]["group"]["loop_group"]["iteration"] == 2
    assert [node_id for node_id, _ in first_executor.rendered] == [
        "producer",
        "first-only",
        "finish",
    ]

    reopened = RunStore(home, max_total_workers=1)

    def recovered_output(context, _rendered):
        node_id = _body_id(context)
        if node_id == "producer":
            return "producer-2"
        if node_id == "finish":
            return "done <promise>DONE</promise>"
        return node_id

    recovered_executor = OutputExecutor(recovered_output)
    recovered_scheduler = RunScheduler(reopened, max_parallel_nodes=1)
    recovered_scheduler.executors["prompt"] = recovered_executor

    result = recovered_scheduler.advance_all([run_id])[run_id]

    assert result["status"] == "succeeded"
    assert [node_id for node_id, _ in recovered_executor.rendered] == [
        "producer",
        "later-only",
        "finish",
    ]
    events = [
        json.loads(line)
        for line in (reopened.run_directory(run_id) / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(
        event["event_type"] == "loop_group_iteration_committed"
        and event["payload"]["completed_iteration"] == 1
        for event in events
    )


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
