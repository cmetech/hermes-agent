from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import threading

import pytest

import plugins.workflow.bash_rendering as bash_rendering
from plugins.workflow.bash_rendering import RenderedBashCommand
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.executors.bash import BashExecutor
from plugins.workflow.models import (
    DeadlineBudget,
    WorkflowLanguageProfile,
    WorkflowNode,
    freeze_value,
)
from plugins.workflow.resources import VariableContext, substitution_renderer
from tools.managed_process import ManagedProcessTree


pytestmark = pytest.mark.skipif(
    os.name == "nt" or not Path("/dev/fd").exists(),
    reason="real POSIX descriptor contract",
)


def _descriptor_snapshot() -> set[int]:
    descriptors: set[int] = set()
    for name in os.listdir("/dev/fd"):
        if not name.isascii() or not name.isdigit():
            continue
        descriptor = int(name)
        try:
            os.fstat(descriptor)
        except OSError:
            # Darwin includes the now-closed directory-stream descriptor in
            # its own /dev/fd listing.  It is not a live process resource.
            continue
        descriptors.add(descriptor)
    return descriptors


def _publisher_threads() -> tuple[threading.Thread, ...]:
    return tuple(
        thread
        for thread in threading.enumerate()
        if thread.name.startswith("hermes-bash-spill-publisher")
    )


def _execute_large(
    tmp_path,
    *,
    value: str | None = None,
    spawn_intent=None,
    deadline_budget=None,
    sealed_attempt_timeout: bool = False,
    monotonic=None,
    process_started=None,
    process_stopped=None,
    spawn_failed=None,
):
    value = value if value is not None else "verified value " + "v" * 32_769
    node = WorkflowNode(
        id="shell",
        node_type="bash",
        value="printf '%s' $USER_MESSAGE",
        depends_on=(),
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )
    renderer = substitution_renderer(
        VariableContext(user_message=value, normalizer_version=3),
        direct_dependencies=(),
    )
    kwargs = {}
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    result = BashExecutor().execute(
        NodeExecutionContext(
            run_id="descriptor-fault",
            run_directory=tmp_path,
            node=node,
            attempt_id="attempt-1",
            variable_context=renderer,
            language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=3,
            spawn_intent=spawn_intent,
            deadline_budget=deadline_budget,
            sealed_attempt_timeout=sealed_attempt_timeout,
            process_started=process_started,
            process_stopped=process_stopped,
            spawn_failed=spawn_failed,
            max_output_bytes=600_000,
            **kwargs,
        )
    )
    stdout = tmp_path / "nodes" / "shell" / "attempt-1" / "stdout.txt"
    return result, stdout.read_bytes() if stdout.exists() else None


def _assert_resources_released(before: set[int]) -> None:
    assert _descriptor_snapshot() <= before
    assert _publisher_threads() == ()


@pytest.mark.parametrize("mutation", ("overwrite", "truncate"))
def test_verified_spill_same_inode_mutation_cannot_reach_the_child(
    tmp_path,
    monkeypatch,
    mutation,
) -> None:
    value = "original metacharacters * $(false) ; " + "v" * 32_769 + "x\n\n"
    expected = value.encode("utf-8")
    retained_writers: list[int] = []
    path_presence: list[bool] = []
    original_open = os.open
    original_spawn = ManagedProcessTree.spawn

    def retain_writer_before_verification(path, flags, mode=0o777, *, dir_fd=None):
        if (
            str(path).startswith("spill-")
            and flags & os.O_ACCMODE == os.O_RDONLY
            and dir_fd is not None
            and not retained_writers
        ):
            retained_writers.append(original_open(path, os.O_WRONLY, dir_fd=dir_fd))
        return original_open(path, flags, mode, dir_fd=dir_fd)

    def mutate_before_spawn(argv, **kwargs):
        spill = (
            tmp_path / "nodes" / "shell" / "attempt-1" / "variables-v3" / "spill-0000"
        )
        path_presence.append(spill.exists())
        writer = retained_writers[0]
        os.lseek(writer, 0, os.SEEK_SET)
        if mutation == "overwrite":
            altered = b"A" * len(expected)
            os.write(writer, altered)
        else:
            os.ftruncate(writer, 0)
            os.write(writer, b"truncated attacker bytes")
        os.fsync(writer)
        return original_spawn(argv, **kwargs)

    monkeypatch.setattr(bash_rendering.os, "open", retain_writer_before_verification)
    monkeypatch.setattr(
        ManagedProcessTree,
        "spawn",
        staticmethod(mutate_before_spawn),
    )
    before = _descriptor_snapshot()
    try:
        result, output = _execute_large(tmp_path, value=value)
    finally:
        for descriptor in retained_writers:
            os.close(descriptor)

    assert retained_writers
    assert path_presence == [False]
    if result.status == "succeeded":
        assert output == expected
        assert result.metadata["bash"]["spill_content_sha256"] == [
            hashlib.sha256(expected).hexdigest()
        ]
    else:
        assert result.error_code == "bash_spill_integrity"
        assert output in (None, b"")
    assert output != b"truncated attacker bytes"
    assert output != b"A" * len(expected)
    _assert_resources_released(before)


def test_large_publication_has_no_spawn_deadlock_and_joins_its_publisher(
    tmp_path,
) -> None:
    value = "metacharacters * $(false) ; " + "p" * 499_972
    assert len(value.encode("utf-8")) == 500_000
    before = _descriptor_snapshot()

    result, output = _execute_large(tmp_path, value=value)

    assert result.status == "succeeded"
    assert output == value.encode("utf-8")
    _assert_resources_released(before)


def test_post_materialization_command_construction_fault_closes_every_descriptor(
    tmp_path,
    monkeypatch,
) -> None:
    before = _descriptor_snapshot()

    def fail_command_construction(*_args, **_kwargs):
        raise RuntimeError("command construction fault")

    monkeypatch.setattr(
        bash_rendering,
        "RenderedBashCommand",
        fail_command_construction,
    )

    with pytest.raises(RuntimeError, match="command construction fault"):
        _execute_large(tmp_path)

    _assert_resources_released(before)


def test_evidence_fault_closes_every_materialized_descriptor(
    tmp_path,
    monkeypatch,
) -> None:
    before = _descriptor_snapshot()

    def fail_evidence(_self):
        raise RuntimeError("evidence fault")

    monkeypatch.setattr(RenderedBashCommand, "evidence", fail_evidence)

    with pytest.raises(RuntimeError, match="evidence fault"):
        _execute_large(tmp_path)

    _assert_resources_released(before)


@pytest.mark.parametrize("stream_name", ("stdout.txt", "stderr.txt"))
def test_output_setup_fault_closes_every_materialized_descriptor(
    tmp_path,
    monkeypatch,
    stream_name,
) -> None:
    before = _descriptor_snapshot()
    original_open = Path.open

    def fail_stream(self, *args, **kwargs):
        if self.name == stream_name:
            raise OSError(f"{stream_name} setup fault")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_stream)

    with pytest.raises(OSError, match="setup fault"):
        _execute_large(tmp_path)

    _assert_resources_released(before)


def test_spawn_intent_callback_fault_closes_output_and_spill_descriptors(
    tmp_path,
) -> None:
    before = _descriptor_snapshot()

    def fail_spawn_intent(_nonce):
        raise RuntimeError("spawn callback fault")

    with pytest.raises(RuntimeError, match="spawn callback fault"):
        _execute_large(tmp_path, spawn_intent=fail_spawn_intent)

    _assert_resources_released(before)


def test_deadline_callback_fault_closes_every_materialized_descriptor(
    tmp_path,
) -> None:
    before = _descriptor_snapshot()
    samples = iter((10.0,))

    def fail_after_materialization():
        try:
            return next(samples)
        except StopIteration as exc:
            raise RuntimeError("deadline callback fault") from exc

    budget = DeadlineBudget.create(
        now=10.0,
        wall_seconds=10.0,
        idle_seconds=10.0,
        provider_seconds=10.0,
    )

    with pytest.raises(RuntimeError, match="deadline callback fault"):
        _execute_large(
            tmp_path,
            deadline_budget=budget,
            sealed_attempt_timeout=True,
            monotonic=fail_after_materialization,
        )

    _assert_resources_released(before)


def test_publisher_write_fault_fails_typed_without_hanging_or_leaking(
    tmp_path,
    monkeypatch,
) -> None:
    before = _descriptor_snapshot()
    original_write = bash_rendering.os.write

    def fail_publisher_write(descriptor, data):
        if (
            threading.current_thread().name == "hermes-bash-spill-publisher"
            and stat.S_ISFIFO(os.fstat(descriptor).st_mode)
        ):
            raise OSError("publisher write fault")
        return original_write(descriptor, data)

    monkeypatch.setattr(bash_rendering.os, "write", fail_publisher_write)

    result, output = _execute_large(tmp_path)

    assert result.status == "failed"
    assert result.error_code == "bash_spill_integrity"
    assert result.metadata["archon_terminal_failure"] is True
    assert output in (b"", None)
    _assert_resources_released(before)


def test_pipe_setup_fault_closes_the_current_pair_and_verified_snapshot(
    tmp_path,
    monkeypatch,
) -> None:
    before = _descriptor_snapshot()

    def fail_pipe_setup(_descriptor, _blocking):
        raise OSError("pipe setup fault")

    monkeypatch.setattr(bash_rendering.os, "set_blocking", fail_pipe_setup)

    with pytest.raises(bash_rendering.BashRenderingError) as exc:
        bash_rendering.render_v3_bash(
            "$USER_MESSAGE",
            ((0, len("$USER_MESSAGE"), "x" * 32_769),),
            spill_directory=tmp_path / "pipe-setup-fault",
        )

    assert exc.value.code == "bash_spill_integrity"
    assert not (tmp_path / "pipe-setup-fault").exists()
    _assert_resources_released(before)


def test_release_then_close_cannot_close_a_reused_descriptor(tmp_path) -> None:
    rendered = bash_rendering.render_v3_bash(
        "$USER_MESSAGE",
        ((0, len("$USER_MESSAGE"), "x" * 32_769),),
        spill_directory=tmp_path / "descriptor-reuse",
    )
    released = rendered.inherited_descriptors[0]
    rendered.release_inherited_descriptors()
    sentinel_path = tmp_path / "sentinel"
    sentinel_path.write_bytes(b"sentinel")
    sentinel = os.open(sentinel_path, os.O_RDONLY)
    if sentinel != released:
        os.dup2(sentinel, released)
        os.close(sentinel)
        sentinel = released
    try:
        assert sentinel == released

        rendered.close()

        assert os.read(sentinel, len(b"sentinel")) == b"sentinel"
    finally:
        os.close(sentinel)

    assert _publisher_threads() == ()


def test_process_started_callback_fault_reaps_child_and_closes_transport(
    tmp_path,
) -> None:
    before = _descriptor_snapshot()

    def fail_process_started(_identity):
        raise RuntimeError("process started callback fault")

    with pytest.raises(RuntimeError, match="process started callback fault"):
        _execute_large(tmp_path, process_started=fail_process_started)

    _assert_resources_released(before)


def test_process_started_rejection_reaps_child_and_closes_transport(tmp_path) -> None:
    before = _descriptor_snapshot()

    result, _output = _execute_large(
        tmp_path,
        process_started=lambda _identity: False,
    )

    assert result.status == "cancelled"
    assert result.error_code == "cancelled"
    _assert_resources_released(before)


def test_spawn_failed_callback_fault_still_closes_publisher_and_output(
    tmp_path,
    monkeypatch,
) -> None:
    before = _descriptor_snapshot()

    def fail_spawn(*_args, **_kwargs):
        raise OSError("spawn boundary fault")

    def fail_spawn_failed(_nonce, _reason):
        raise RuntimeError("spawn failed callback fault")

    monkeypatch.setattr(
        ManagedProcessTree,
        "spawn",
        staticmethod(fail_spawn),
    )

    with pytest.raises(RuntimeError, match="spawn failed callback fault"):
        _execute_large(tmp_path, spawn_failed=fail_spawn_failed)

    _assert_resources_released(before)


def test_process_stopped_callback_fault_keeps_all_owners_exception_safe(
    tmp_path,
) -> None:
    before = _descriptor_snapshot()

    def fail_process_stopped(_identity, _reaped):
        raise RuntimeError("process stopped callback fault")

    with pytest.raises(RuntimeError, match="process stopped callback fault"):
        _execute_large(tmp_path, process_stopped=fail_process_stopped)

    _assert_resources_released(before)
