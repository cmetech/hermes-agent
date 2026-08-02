from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil
import time

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.entitlement import AIEntitlementResolution
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.executors.script import ScriptExecutor
from plugins.workflow.models import WorkflowLanguageProfile, WorkflowNode, freeze_value
from plugins.workflow.output_resolution import (
    ResolvedNodeOutput,
    WorkflowOutputReferenceError,
)
from plugins.workflow.resources import ResourceResolver, VariableContext
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
from tools.managed_process import ProcessResourceLimits, TerminationPolicy


def test_node_execution_context_preserves_pre_sealed_resource_positional_order(
    tmp_path: Path,
) -> None:
    node = WorkflowNode(
        id="script",
        node_type="script",
        value="print('ok')\n",
        depends_on=(),
        source_index=0,
        source_line=1,
        options=freeze_value({"runtime": "uv", "deps": ()}),
    )
    monotonic = lambda: 17.0
    termination_policy = TerminationPolicy(
        cooperative_grace_seconds=1,
        term_grace_seconds=2,
        kill_grace_seconds=3,
        wait_timeout_seconds=4,
    )

    context = NodeExecutionContext(
        "run-1",
        tmp_path,
        node,
        "attempt-1",
        10.0,
        1024,
        2048,
        None,
        "workflow",
        {},
        None,
        {},
        {},
        "local",
        AIEntitlementResolution("real"),
        None,
        ProcessResourceLimits(),
        None,
        5,
        None,
        None,
        None,
        None,
        None,
        None,
        monotonic,
        termination_policy,
    )

    assert context.monotonic is monotonic
    assert context.termination_policy is termination_policy
    assert context.sealed_resource_paths is None
    assert context.sealed_resource_bytes is None


def test_named_script_prefers_exact_package_resource_before_runtime_suffix(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    scripts = package / "scripts"
    scripts.mkdir(parents=True)
    exact = scripts / "diagnose"
    exact.write_text("print('exact')\n", encoding="utf-8")
    (scripts / "diagnose.py").write_text("print('suffix')\n", encoding="utf-8")

    resource = ResourceResolver(package).script("diagnose", runtime="uv")

    assert resource.path == exact.resolve()
    assert resource.runtime == "uv"


def test_named_script_ignores_unsealed_extensionless_shadow(tmp_path: Path) -> None:
    package = tmp_path / "package"
    scripts = package / "scripts"
    scripts.mkdir(parents=True)
    shadow = scripts / "diagnose"
    shadow.write_text("print('shadow')\n", encoding="utf-8")
    sealed = scripts / "diagnose.py"
    sealed.write_text("print('sealed')\n", encoding="utf-8")

    resource = ResourceResolver(
        package, sealed_paths={"scripts/diagnose.py"}
    ).script("diagnose", runtime="uv")

    assert resource.path == sealed.resolve()


def test_script_executor_resolves_only_scheduler_verified_resources(
    tmp_path: Path,
) -> None:
    scripts = tmp_path / "run" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "diagnose").write_text("print('shadow')\n", encoding="utf-8")
    sealed = scripts / "diagnose.py"
    sealed.write_text("print('sealed')\n", encoding="utf-8")
    context = replace(
        _context(tmp_path, runtime="uv", script="diagnose"),
        sealed_resource_paths=frozenset({"scripts/diagnose.py"}),
    )

    argv, _warnings = ScriptExecutor()._argv(context, "/fake/uv")

    assert argv[-1] == str(sealed.resolve())


@pytest.mark.parametrize("mutation", ["delete", "rename", "replace"])
def test_script_executor_uses_authenticated_bytes_without_reopening_source(
    tmp_path: Path, mutation: str
) -> None:
    scripts = tmp_path / "run" / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "diagnose.py"
    authenticated = b"print('authenticated')\n"
    script.write_bytes(authenticated)
    context = replace(
        _context(tmp_path, runtime="uv", script="diagnose"),
        sealed_resource_paths=frozenset({"scripts/diagnose.py"}),
        sealed_resource_bytes={"scripts/diagnose.py": authenticated},
    )
    if mutation == "delete":
        script.unlink()
    elif mutation == "rename":
        script.rename(script.with_suffix(".gone"))
    else:
        script.write_text("print('forged')\n", encoding="utf-8")

    argv, _warnings, source = ScriptExecutor()._execution_plan(context, "/fake/uv")

    assert argv[-2:] == ["python", "-"]
    assert source == authenticated


@pytest.mark.parametrize(
    ("runtime", "authenticated_source", "forged_source"),
    [
        ("uv", b"print('authenticated-child')\n", b"print('forged-child')\n"),
        (
            "bun",
            b"console.log('authenticated-child')\n",
            b"console.log('forged-child')\n",
        ),
    ],
)
def test_named_script_child_reads_authenticated_bytes_not_raced_original(
    tmp_path: Path,
    runtime: str,
    authenticated_source: bytes,
    forged_source: bytes,
) -> None:
    real_runtime = shutil.which(runtime)
    if real_runtime is None:
        pytest.skip(f"{runtime} is not installed")
    suffix = ".py" if runtime == "uv" else ".js"
    script = tmp_path / "run" / "scripts" / f"race{suffix}"
    script.parent.mkdir(parents=True)
    script.write_bytes(authenticated_source)
    wrapper = tmp_path / f"race-{runtime}-wrapper.py"
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib,subprocess,sys\n"
        "source=sys.stdin.buffer.read()\n"
        f"target=pathlib.Path({str(script)!r})\n"
        f"target.write_bytes({forged_source!r})\n"
        "if not source: source=target.read_bytes()\n"
        + (
            "exec(compile(source, str(target), 'exec'))\n"
            if runtime == "uv"
            else (
                f"raise SystemExit(subprocess.run([{real_runtime!r}, "
                "'--no-env-file', 'run', '-'], input=source).returncode)\n"
            )
        ),
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    context = replace(
        _context(tmp_path, runtime=runtime, script=f"race{suffix}"),
        sealed_resource_paths=frozenset({f"scripts/race{suffix}"}),
        sealed_resource_bytes={f"scripts/race{suffix}": authenticated_source},
    )

    result = ScriptExecutor(
        runtime_locator=lambda _runtime: str(wrapper)
    ).execute(context)

    assert script.read_bytes() == forged_source
    assert result.status == "succeeded"
    output = context.run_directory / result.artifacts[0].relative_path
    assert output.read_text() == "authenticated-child"


@pytest.mark.parametrize(
    ("name", "runtime", "message"),
    [
        ("../escape", "uv", "contained script name"),
        ("diagnose.js", "uv", "requires a Python script"),
        ("diagnose.py", "bun", "requires a JavaScript or TypeScript script"),
        ("diagnose", "node", "runtime must be bun or uv"),
    ],
)
def test_named_script_rejects_traversal_and_runtime_extension_mismatch(
    tmp_path: Path, name: str, runtime: str, message: str
) -> None:
    scripts = tmp_path / "package" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "diagnose.js").write_text("console.log('js')\n", encoding="utf-8")
    (scripts / "diagnose.py").write_text("print('py')\n", encoding="utf-8")

    with pytest.raises((ValueError, FileNotFoundError), match=message):
        ResourceResolver(tmp_path / "package").script(name, runtime=runtime)


def test_named_script_does_not_follow_symlink_outside_package(tmp_path: Path) -> None:
    package = tmp_path / "package"
    scripts = package / "scripts"
    scripts.mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    (scripts / "diagnose.py").symlink_to(outside)

    with pytest.raises(FileNotFoundError, match="missing"):
        ResourceResolver(package).script("diagnose", runtime="uv")


def _context(
    tmp_path: Path,
    *,
    runtime: str,
    script: str,
    deps: tuple[str, ...] = (),
    timeout_seconds: float = 3,
    variable_context: VariableContext | None = None,
    depends_on: tuple[str, ...] = (),
    termination_policy: TerminationPolicy | None = None,
) -> NodeExecutionContext:
    run_directory = tmp_path / "run"
    run_directory.mkdir(exist_ok=True)
    node = WorkflowNode(
        id="script",
        node_type="script",
        value=script,
        depends_on=depends_on,
        source_index=0,
        source_line=1,
        options=freeze_value({"runtime": runtime, "deps": deps}),
    )
    return NodeExecutionContext(
        run_id="run-1",
        run_directory=run_directory,
        node=node,
        attempt_id="attempt-1",
        timeout_seconds=timeout_seconds,
        variable_context=variable_context,
        **(
            {"termination_policy": termination_policy}
            if termination_policy is not None
            else {}
        ),
    )


def test_v3_inline_script_rechecks_direct_dependency_before_runtime(tmp_path: Path) -> None:
    variables = VariableContext(
        normalizer_version=3,
        node_outputs={
            "producer": ResolvedNodeOutput(
                canonical_bytes=b'{"answer":"ready"}',
                value={"answer": "ready"},
                text='{"answer":"ready"}',
                media_type="application/json",
                sha256="1" * 64,
                node_id="producer",
                attempt_id="attempt-winner",
                publication_id="a" * 32,
                schema_fingerprint="3" * 64,
                canonicalization_version=1,
            )
        },
    )
    context = replace(
        _context(
            tmp_path,
            runtime="uv",
            script="print('$producer.output.answer')\n",
            variable_context=variables,
        ),
        language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
    )

    with pytest.raises(WorkflowOutputReferenceError) as exc:
        ScriptExecutor(runtime_locator=lambda _runtime: "/fake/uv").execute(context)

    assert exc.value.code == "output_reference_not_declared_dependency"
    assert not (context.run_directory / "nodes").exists()


@pytest.mark.parametrize("runtime", ("uv", "bun"))
def test_legacy_missing_named_script_preserves_attempt_tree_before_validation(
    tmp_path: Path,
    runtime: str,
) -> None:
    context = _context(tmp_path, runtime=runtime, script="missing")

    result = ScriptExecutor(runtime_locator=lambda selected: f"/fake/{selected}").execute(
        context
    )

    assert result.error_code == "validation"
    attempt = context.run_directory / "nodes" / context.node.id / context.attempt_id
    assert attempt.is_dir()
    assert (context.run_directory / "artifacts").is_dir()


def test_v3_named_script_bytes_are_never_interpolated(tmp_path: Path) -> None:
    source = b"print('$producer.output.answer')\n"
    variables = VariableContext(
        normalizer_version=3,
        node_outputs={
            "producer": ResolvedNodeOutput(
                canonical_bytes=b'{"answer":"ready"}',
                value={"answer": "ready"},
                text='{"answer":"ready"}',
                media_type="application/json",
                sha256="1" * 64,
                node_id="producer",
                attempt_id="attempt-winner",
                publication_id="a" * 32,
                schema_fingerprint="3" * 64,
                canonicalization_version=1,
            )
        },
    )
    context = replace(
        _context(
            tmp_path,
            runtime="uv",
            script="named",
            variable_context=variables,
            depends_on=("producer",),
        ),
        sealed_resource_paths=frozenset({"scripts/named.py"}),
        sealed_resource_bytes={"scripts/named.py": source},
    )

    _argv, _warnings, source_bytes = ScriptExecutor()._execution_plan(
        context,
        "/fake/uv",
    )

    assert source_bytes == source


def test_uv_dependencies_are_distinct_argv_without_shell_interpolation(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "must-not-exist"
    dependency = f"demo; touch {marker}"
    wrapper = tmp_path / "fake-uv"
    wrapper.write_text(
        "#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    context = _context(
        tmp_path,
        runtime="uv",
        script="print('never')\n",
        deps=(dependency,),
    )

    result = ScriptExecutor(runtime_locator=lambda _runtime: str(wrapper)).execute(
        context
    )

    assert result.status == "succeeded"
    output = context.run_directory / result.artifacts[0].relative_path
    assert json.loads(output.read_text()) == [
        "run",
        "--no-project",
        "--with",
        dependency,
        "python",
        "-c",
        "print('never')\n",
    ]
    assert not marker.exists()


def test_inline_script_renders_frozen_nested_objects_and_arrays(tmp_path: Path) -> None:
    wrapper = tmp_path / "fake-uv"
    wrapper.write_text(
        "#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    canonical = b'{"items":[{"count":3}]}'
    context = _context(
        tmp_path,
        runtime="uv",
        script=(
            "print('$collect.output.items.0', "
            "'$collect.output.items')\n"
        ),
        variable_context=VariableContext(
            node_outputs={
                "collect": ResolvedNodeOutput(
                    canonical_bytes=canonical,
                    value={"items": [{"count": 3}]},
                    text=canonical.decode("utf-8"),
                    media_type="application/json",
                    sha256="1" * 64,
                    node_id="collect",
                    attempt_id="attempt-winner",
                    publication_id=None,
                )
            }
        ),
    )

    result = ScriptExecutor(runtime_locator=lambda _runtime: str(wrapper)).execute(
        context
    )

    assert result.status == "succeeded"
    output = context.run_directory / result.artifacts[0].relative_path
    assert json.loads(output.read_text())[-1] == (
        'print(\'{"count":3}\', \'[{"count":3}]\')\n'
    )


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_extensionless_named_uv_script_runs_as_python_outside_a_project(
    tmp_path: Path,
) -> None:
    scripts = tmp_path / "run" / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "extensionless"
    script.write_text("print('extensionless-ok')\n", encoding="utf-8")
    context = _context(tmp_path, runtime="uv", script="extensionless")

    result = ScriptExecutor().execute(context)

    assert result.status == "succeeded"
    output = context.run_directory / result.artifacts[0].relative_path
    assert output.read_text() == "extensionless-ok"


@pytest.mark.parametrize(
    ("runtime", "source"),
    [
        (
            "uv",
            "import json,sys; print(json.dumps({'ok': True})); "
            "print('diagnostic', file=sys.stderr)\n",
        ),
        (
            "bun",
            "console.log(JSON.stringify({ok:true})); console.error('diagnostic');\n",
        ),
    ],
)
def test_inline_script_captures_json_stdout_and_diagnostic_stderr(
    tmp_path: Path, runtime: str, source: str
) -> None:
    if shutil.which(runtime) is None:
        pytest.skip(f"{runtime} is not installed")
    context = _context(tmp_path, runtime=runtime, script=source)

    result = ScriptExecutor().execute(context)

    assert result.status == "succeeded"
    assert [artifact.media_type for artifact in result.artifacts] == [
        "application/json",
        "text/plain",
    ]
    output = context.run_directory / result.artifacts[0].relative_path
    stderr = context.run_directory / result.artifacts[1].relative_path
    assert json.loads(output.read_text()) == {"ok": True}
    assert stderr.read_text().strip() == "diagnostic"


def test_missing_script_runtime_is_a_typed_failure(tmp_path: Path) -> None:
    result = ScriptExecutor(runtime_locator=lambda _runtime: None).execute(
        _context(tmp_path, runtime="uv", script="print('no runtime')\n")
    )

    assert result.status == "failed"
    assert result.error_code == "runtime_missing"
    assert "uv" in result.error_message


def test_named_script_receives_sanitized_workflow_variable_environment(
    tmp_path: Path,
) -> None:
    scripts = tmp_path / "run" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "environment.py").write_text(
        "import json,os\n"
        "print(json.dumps({'arguments': os.environ['ARGUMENTS'], "
        "'workflow': os.environ['WORKFLOW_ID']}))\n",
        encoding="utf-8",
    )
    context = _context(
        tmp_path,
        runtime="uv",
        script="environment",
        variable_context=VariableContext(
            arguments="safe evidence", workflow_id="run-1"
        ),
    )

    result = ScriptExecutor().execute(context)

    output = context.run_directory / result.artifacts[0].relative_path
    assert result.status == "succeeded"
    assert json.loads(output.read_text()) == {
        "arguments": "safe evidence",
        "workflow": "run-1",
    }


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
@pytest.mark.live_system_guard_bypass
def test_script_timeout_reaps_spawned_descendant(tmp_path: Path) -> None:
    source = (
        "import subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(30)\n"
    )
    context = _context(
        tmp_path,
        runtime="uv",
        script=source,
        timeout_seconds=0.2,
        termination_policy=TerminationPolicy(
            cooperative_grace_seconds=0,
            term_grace_seconds=1.0,
            kill_grace_seconds=1.0,
            wait_timeout_seconds=2.0,
        ),
    )

    result = ScriptExecutor().execute(context)

    assert result.status == "failed"
    assert result.error_code == "timeout"
    output = context.run_directory / result.artifacts[0].relative_path
    child_pid = int(output.read_text())
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            import psutil

            child = psutil.Process(child_pid)
            if not child.is_running() or child.status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.02)
    else:
        pytest.fail(f"script descendant {child_pid} survived timeout cleanup")


def test_scheduler_executes_snapshotted_named_script(
    tmp_path: Path, workflow_writer
) -> None:
    package_root = tmp_path / "package"
    scripts = package_root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "summarize.py").write_text(
        "import json; print(json.dumps({'status':'ok'}))\n", encoding="utf-8"
    )
    workflow = workflow_writer(
        package_root / "workflows",
        name="script-e2e",
        nodes=[
            {
                "id": "summarize",
                "script": "summarize",
                "runtime": "uv",
                "output_type": "ScriptSummary",
            }
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="script-e2e",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )

    result = RunScheduler(store).advance(admitted.run_id)

    assert result["status"] == "succeeded"
    artifact = result["artifacts"][0]
    output = store.run_directory(admitted.run_id) / artifact["relative_path"]
    assert json.loads(output.read_text()) == {"status": "ok"}
    assert artifact["publication_id"]
    bundle = (
        store.run_directory(admitted.run_id)
        / "publications"
        / artifact["publication_id"]
    )
    assert artifact["media_type"] == "application/json"
    assert (bundle / "content.json").read_bytes() == output.read_bytes()
