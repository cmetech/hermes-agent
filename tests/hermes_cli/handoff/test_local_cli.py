from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import hermes_cli.handoff.local as local_module
from hermes_cli.handoff.local import LocalHermesChannel
from hermes_cli.handoff.models import HandoffEndpoint, HandoffSnapshot, HandoffSpec
from tools.managed_process import ProcessIdentity


def _snapshot(tmp_path: Path, monkeypatch, *, phase="prepared", checkpoint=None):
    home = tmp_path / ".hermes"
    (home / "profiles" / "reviewer").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    spec = HandoffSpec(
        mode="task",
        endpoint=HandoffEndpoint.parse("hermes://local/reviewer"),
        prompt="review $(touch /tmp/nope); `echo nope` ' \" & | < >",
        output_schema=None,
        deadline_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        attribution={},
        required_capabilities=frozenset(),
    )
    return HandoffSnapshot(
        handoff_id="handoff-1",
        key_scope="workflow/run-1",
        handoff_key="review",
        spec=spec,
        spec_fingerprint=spec.fingerprint,
        phase=phase,
        state_version=1,
        mechanism="local_cli" if phase != "prepared" else None,
        binding=(
            {"profile": "reviewer", "mechanism": "local_cli"}
            if phase != "prepared"
            else None
        ),
        checkpoint=checkpoint,
    )


def _bound(tmp_path, monkeypatch):
    snapshot = _snapshot(tmp_path, monkeypatch)
    channel = LocalHermesChannel()
    monkeypatch.setattr(channel, "_assess", lambda *_a: (None, "runs_not_durable"))
    observation = channel.bind(snapshot, budget_seconds=1)
    return channel, replace(
        snapshot,
        mechanism=observation.mechanism,
        binding=observation.binding,
        checkpoint=observation.checkpoint,
    )


def _write_receipt(snapshot, *, stdout=b"done", stderr=b"", exit_code=0):
    paths = local_module._cli_paths(snapshot.handoff_id)
    local_module._atomic_bytes(paths.stdout, stdout)
    local_module._atomic_bytes(paths.stderr, stderr)
    receipt = {
        "version": 1,
        "handoff_id": snapshot.handoff_id,
        "profile": snapshot.spec.endpoint.profile,
        "request_sha256": snapshot.checkpoint["request_sha256"],
        "exit_code": exit_code,
        "stdout_size": len(stdout),
        "stderr_size": len(stderr),
        "stdout_sha256": sha256(stdout).hexdigest(),
        "stderr_sha256": sha256(stderr).hexdigest(),
    }
    local_module._atomic_json(paths.receipt, receipt)
    return paths, receipt


def test_cli_platform_gate_is_pure_data_and_windows_is_stable():
    assert local_module._local_cli_failure(frozenset(), host_os="posix") is None
    assert (
        local_module._local_cli_failure(frozenset(), host_os="nt")
        == "local_cli_lock_unavailable"
    )


@pytest.mark.windows_only
def test_windows_binding_refuses_cli_without_faking_host(tmp_path, monkeypatch):
    snapshot = _snapshot(tmp_path, monkeypatch)
    channel = LocalHermesChannel()
    monkeypatch.setattr(channel, "_assess", lambda *_a: (None, "runs_not_durable"))

    observation = channel.bind(snapshot, budget_seconds=1)
    assert observation.mechanism is None
    assert observation.failure_code == "local_cli_lock_unavailable"


def test_bind_uses_cli_only_after_authoritative_runs_unavailability(tmp_path, monkeypatch):
    snapshot = _snapshot(tmp_path, monkeypatch)
    channel = LocalHermesChannel()
    monkeypatch.setattr(channel, "_assess", lambda *_a: (None, "endpoint_unavailable"))
    uncertain = channel.bind(snapshot, budget_seconds=1)
    monkeypatch.setattr(channel, "_assess", lambda *_a: (None, "runs_not_durable"))
    bound = channel.bind(snapshot, budget_seconds=1)

    assert uncertain.mechanism is None
    assert bound.binding == {"profile": "reviewer", "mechanism": "local_cli"}
    assert set(bound.checkpoint) == {"request_sha256"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX wrapper and flock contract")
def test_wrapper_keeps_prompt_out_of_argv_holds_profile_lock_and_bounds_output(
    tmp_path, monkeypatch
):
    _channel, snapshot = _bound(tmp_path, monkeypatch)
    seen = {}

    class _Lock:
        def __enter__(self):
            seen["locked"] = True

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(local_module, "acquire_turn_lock", lambda *_a, **_k: _Lock())

    def run(argv, **kwargs):
        seen["argv"] = argv
        assert seen["locked"]
        assert snapshot.spec.prompt not in "\0".join(argv)
        assert Path(argv[argv.index("--query-file") + 1]).read_text() == snapshot.spec.prompt
        return SimpleNamespace(returncode=0, stdout=b"x" * (local_module.MAX_CLI_OUTPUT_BYTES + 50), stderr=b"")

    monkeypatch.setattr(local_module.subprocess, "run", run)
    assert local_module._run_cli_wrapper(snapshot.handoff_id, "reviewer") == 0
    observation = local_module.LocalHermesChannel().observe(snapshot, budget_seconds=1)

    assert seen["argv"][seen["argv"].index("-c") + 1] == "Handoff: handoff-1"
    assert observation.phase == "succeeded"
    assert observation.terminal_result["size_bytes"] == local_module.MAX_CLI_OUTPUT_BYTES


def test_wrapper_refuses_symlinked_handoff_spool(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / "handoffs").mkdir(parents=True)
    (home / "handoffs" / "handoff-1").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("HERMES_HOME", str(home))

    with pytest.raises(ValueError, match="unsafe"):
        local_module._cli_paths("handoff-1")
    assert list(outside.iterdir()) == []


def test_signal_exit_cannot_be_recorded_as_success(tmp_path, monkeypatch):
    channel, snapshot = _bound(tmp_path, monkeypatch)
    monkeypatch.setattr(
        local_module.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=-9, stdout=b"", stderr=b""),
    )

    local_module._run_cli_wrapper(snapshot.handoff_id, "reviewer")
    observed = channel.observe(snapshot, budget_seconds=1)
    assert observed.phase == "failed"
    assert observed.checkpoint["exit_code"] == 137


def test_submit_returns_process_identity_without_absolute_paths(tmp_path, monkeypatch):
    channel, snapshot = _bound(tmp_path, monkeypatch)
    identity = ProcessIdentity(pid=4321, start_time=9876, group_id=4321)
    monkeypatch.setattr(
        local_module.ManagedProcessTree,
        "spawn",
        lambda argv, **kwargs: SimpleNamespace(identity=identity),
    )
    observation = channel.submit(snapshot, budget_seconds=1)

    assert observation.phase == "submitted"
    assert observation.checkpoint["process_pid"] == 4321
    assert observation.checkpoint["process_started_at"] == 9876
    assert set(observation.checkpoint) == {
        "request_sha256", "process_pid", "process_started_at",
        "process_command_sha256", "status",
    }
    assert str(tmp_path) not in json.dumps(dict(observation.checkpoint))


def test_receipt_is_observed_after_initiator_restart_before_process_identity(
    tmp_path, monkeypatch
):
    _channel, snapshot = _bound(tmp_path, monkeypatch)
    snapshot = replace(snapshot, phase="submitted", checkpoint={
        **snapshot.checkpoint,
        "process_pid": 999999,
        "process_started_at": 1,
        "process_command_sha256": "a" * 64,
        "status": "running",
    })
    _write_receipt(snapshot, stdout=b"durable reply")
    monkeypatch.setattr(ProcessIdentity, "is_current", lambda self: (_ for _ in ()).throw(AssertionError("receipt must win")))

    observed = LocalHermesChannel().observe(snapshot, budget_seconds=1)
    assert observed.phase == "succeeded"
    assert observed.terminal_result["text"] == "durable reply"
    assert {"receipt_sha256", "stdout_sha256", "stderr_sha256", "exit_code"} <= set(observed.checkpoint)


def test_tampered_receipt_and_reused_or_dead_pid_are_indeterminate(tmp_path, monkeypatch):
    _channel, snapshot = _bound(tmp_path, monkeypatch)
    snapshot = replace(snapshot, phase="submitted", checkpoint={
        **snapshot.checkpoint,
        "process_pid": 123,
        "process_started_at": 456,
        "process_command_sha256": "a" * 64,
        "status": "running",
    })
    paths, _receipt = _write_receipt(snapshot)
    paths.stdout.write_text("tampered", encoding="utf-8")
    monkeypatch.setattr(ProcessIdentity, "is_current", lambda self: False)

    observed = LocalHermesChannel().observe(snapshot, budget_seconds=1)
    assert observed.phase == "indeterminate"
    assert observed.failure_code == "local_cli_process_lost"


def test_reused_process_group_is_not_the_recorded_identity(tmp_path, monkeypatch):
    _channel, snapshot = _bound(tmp_path, monkeypatch)
    snapshot = replace(snapshot, phase="submitted", checkpoint={
        **snapshot.checkpoint,
        "process_pid": 123,
        "process_started_at": 456,
        "process_command_sha256": "a" * 64,
        "status": "running",
    })
    monkeypatch.setattr(
        ProcessIdentity,
        "capture",
        classmethod(lambda cls, pid: ProcessIdentity(pid, 456, 999)),
    )
    monkeypatch.setattr(ProcessIdentity, "is_current", lambda self: True)

    observed = LocalHermesChannel().observe(snapshot, budget_seconds=1)
    assert observed.phase == "indeterminate"


def test_reconcile_never_blindly_replays_a_local_cli_submission(tmp_path, monkeypatch):
    channel, snapshot = _bound(tmp_path, monkeypatch)
    snapshot = replace(snapshot, phase="indeterminate", checkpoint={
        **snapshot.checkpoint,
        "process_pid": 123,
        "process_started_at": 456,
        "process_command_sha256": "a" * 64,
        "status": "running",
    })
    monkeypatch.setattr(ProcessIdentity, "is_current", lambda self: False)
    monkeypatch.setattr(channel, "_submit_cli", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("blind replay")))
    assert channel.reconcile(snapshot, budget_seconds=1).phase == "indeterminate"


def test_timeout_writes_error_receipt_and_cancel_needs_authoritative_stop(tmp_path, monkeypatch):
    channel, snapshot = _bound(tmp_path, monkeypatch)
    monkeypatch.setattr(
        local_module.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(subprocess.TimeoutExpired(["hermes"], 1)),
    )
    local_module._run_cli_wrapper(snapshot.handoff_id, "reviewer")
    assert channel.observe(snapshot, budget_seconds=1).phase == "failed"

    paths = local_module._cli_paths(snapshot.handoff_id)
    paths.receipt.unlink()
    submitted = replace(snapshot, phase="cancelling", checkpoint={
        **snapshot.checkpoint,
        "process_pid": 123,
        "process_started_at": 456,
        "process_command_sha256": "a" * 64,
        "status": "running",
    })
    monkeypatch.setattr(channel, "_identity_is_current", lambda identity: True)
    monkeypatch.setattr(local_module.ManagedProcessTree, "terminate_existing", lambda identity: False)
    assert channel.cancel(submitted, budget_seconds=1).phase == "cancelling"
    monkeypatch.setattr(local_module.ManagedProcessTree, "terminate_existing", lambda identity: True)
    assert channel.cancel(submitted, budget_seconds=1).phase == "cancelled"
