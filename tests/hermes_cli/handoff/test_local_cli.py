from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

import hermes_cli.handoff.local as local_module
from hermes_cli.handoff.local import LocalHermesChannel
from hermes_cli.handoff.models import (
    ChannelObservation,
    HandoffEndpoint,
    HandoffSnapshot,
    HandoffSpec,
)
from hermes_cli.handoff.service import AgentHandoffService
from hermes_cli.handoff.store import HandoffStore
from tools.managed_process import ManagedProcessTree, ProcessIdentity


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


def _conversation(snapshot, *, controlled: bool):
    spec = HandoffSpec(
        mode="conversation",
        endpoint=snapshot.spec.endpoint,
        prompt=snapshot.spec.prompt,
        output_schema=None,
        deadline_at=snapshot.spec.deadline_at,
        attribution={"sender": "default"},
        required_capabilities=(
            frozenset({"cancellation", "follow_up"})
            if controlled
            else frozenset()
        ),
        return_route={
            "kind": "operator",
            "profile": "default",
            "inbox_id": "desktop-1",
        },
    )
    return replace(snapshot, spec=spec, spec_fingerprint=spec.fingerprint)


def _write_receipt(
    snapshot, *, stdout=b"done", stderr=b"", exit_code=0, outcome="completed"
):
    paths = local_module._cli_paths(
        Path(os.environ["HERMES_HOME"]), snapshot.handoff_id
    )
    local_module._atomic_bytes(paths.stdout, stdout)
    receipt = {
        "version": local_module._CLI_RECEIPT_VERSION,
        "handoff_id": snapshot.handoff_id,
        "profile": snapshot.spec.endpoint.profile,
        "request_sha256": snapshot.checkpoint["request_sha256"],
        "outcome": outcome,
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


def test_legacy_conversation_binds_existing_bot_chat_cli_even_when_runs_exists(
    tmp_path, monkeypatch
):
    snapshot = _conversation(_snapshot(tmp_path, monkeypatch), controlled=False)
    channel = LocalHermesChannel()
    monkeypatch.setattr(
        channel,
        "_assess",
        lambda *_args: (local_module._Connection("http://local", "secret"), None),
    )

    bound = channel.bind(snapshot, budget_seconds=1)

    assert bound.mechanism == "local_bot_cli"
    assert bound.binding == {"profile": "reviewer", "mechanism": "local_bot_cli"}
    assert local_module._wrapper_argv(
        snapshot.handoff_id, "reviewer", "Bot Chat"
    )[-1] == "Bot Chat"


def test_controlled_conversation_requires_runs_and_never_falls_back_to_cli(
    tmp_path, monkeypatch
):
    snapshot = _conversation(_snapshot(tmp_path, monkeypatch), controlled=True)
    channel = LocalHermesChannel()
    monkeypatch.setattr(
        channel,
        "_assess_conversation",
        lambda *_args: (None, frozenset(), "capability_mismatch"),
        raising=False,
    )

    refused = channel.bind(snapshot, budget_seconds=1)

    assert refused.mechanism is None
    assert refused.failure_code == "capability_mismatch"
    assert not (
        Path(os.environ["HERMES_HOME"])
        / "handoffs"
        / snapshot.handoff_id
        / "prompt.txt"
    ).exists()


def test_controlled_conversation_seals_runs_capabilities_and_bot_chat_session(
    tmp_path, monkeypatch
):
    snapshot = _conversation(_snapshot(tmp_path, monkeypatch), controlled=True)
    channel = LocalHermesChannel()
    connection = local_module._Connection("http://local", "secret")
    capabilities = frozenset({
        "authoritative_status",
        "cancellation",
        "durable_admission",
        "follow_up",
    })
    monkeypatch.setattr(
        channel,
        "_assess_conversation",
        lambda *_args: (connection, capabilities, None),
        raising=False,
    )
    seen = {}

    def ensure_session(_client, title, *, source):
        seen.update(title=title, source=source)
        return "session-1"

    monkeypatch.setattr(local_module.RunsClient, "ensure_session", ensure_session)

    bound = channel.bind(snapshot, budget_seconds=1)

    assert seen == {"title": "Bot Chat", "source": "bot_handoff"}
    assert bound.mechanism == "runs"
    assert bound.binding == {
        "profile": "reviewer",
        "mechanism": "runs",
        "capabilities": tuple(sorted(capabilities)),
    }
    assert bound.checkpoint == {"session_id": "session-1"}


def test_controlled_local_conversation_delivers_follow_up_through_bound_run(
    tmp_path, monkeypatch
):
    snapshot = replace(
        _conversation(_snapshot(tmp_path, monkeypatch), controlled=True),
        phase="active",
        mechanism="runs",
        binding={
            "profile": "reviewer",
            "mechanism": "runs",
            "capabilities": [
                "authoritative_status",
                "cancellation",
                "durable_admission",
                "follow_up",
            ],
        },
        checkpoint={"run_id": "run-1", "status": "running"},
    )
    channel = LocalHermesChannel()
    monkeypatch.setattr(
        channel,
        "_bound_connection",
        lambda *_args: local_module._Connection("http://local", "secret"),
    )
    calls = []
    monkeypatch.setattr(
        local_module.RunsClient,
        "steer",
        lambda _client, run_id, text: calls.append((run_id, text))
        or {"accepted": True},
    )

    result = channel.deliver_command(
        snapshot,
        SimpleNamespace(
            kind="message",
            delivery_state="pending",
            payload={"text": "Check this."},
        ),
        budget_seconds=1,
    )

    assert result == ("delivered", None)
    assert calls == [("run-1", "Check this.")]


def test_default_service_anchors_cli_spool_to_its_store_home(tmp_path, monkeypatch):
    root = tmp_path / ".hermes"
    source_home = root / "profiles" / "initiator"
    ambient_home = root / "profiles" / "ambient"
    for path in (source_home, ambient_home, root / "profiles" / "reviewer"):
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(ambient_home))
    store = HandoffStore(source_home / "handoffs.db")
    service = AgentHandoffService(store)
    spec = HandoffSpec(
        mode="task",
        endpoint=HandoffEndpoint.parse("hermes://local/reviewer"),
        prompt="profile-local prompt",
        output_schema=None,
        deadline_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        attribution={},
        required_capabilities=frozenset(),
    )
    snapshot = service.create(
        spec,
        "workflow/source-run",
        handoff_key="review/0",
    )
    monkeypatch.setattr(
        service.channel.local,
        "_assess",
        lambda *_args: (None, "runs_not_durable"),
    )

    bound = service.advance(snapshot.handoff_id).snapshot

    assert (source_home / "handoffs" / snapshot.handoff_id / "prompt.txt").is_file()
    assert not (ambient_home / "handoffs" / snapshot.handoff_id).exists()

    identity = ProcessIdentity(pid=4321, start_time=9876, group_id=4321)

    def spawn(_argv, **kwargs):
        assert kwargs["env"]["HERMES_HOME"] == str(source_home.resolve())
        return SimpleNamespace(identity=identity)

    monkeypatch.setattr(local_module.ManagedProcessTree, "spawn", spawn)
    submitted = service.advance(bound.handoff_id).snapshot
    assert submitted.checkpoint["process_pid"] == identity.pid


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

    class _Process:
        returncode = 0
        stdout = io.BytesIO(b"x" * (local_module.MAX_CLI_OUTPUT_BYTES + 50))
        stderr = io.BytesIO()

        def wait(self, timeout):
            return self.returncode

    def popen(argv, **kwargs):
        seen["argv"] = argv
        assert seen["locked"]
        assert snapshot.spec.prompt not in "\0".join(argv)
        assert Path(argv[argv.index("--query-file") + 1]).read_text() == snapshot.spec.prompt
        assert kwargs["pass_fds"]
        assert "start_new_session" not in kwargs
        assert "process_group" not in kwargs
        return _Process()

    monkeypatch.setattr(
        local_module.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("unbounded run")),
    )
    monkeypatch.setattr(local_module.subprocess, "Popen", popen)
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
        local_module._cli_paths(home, "handoff-1")
    assert list(outside.iterdir()) == []


def test_pinned_spool_directory_survives_path_replacement(tmp_path, monkeypatch):
    _channel, snapshot = _bound(tmp_path, monkeypatch)
    paths = local_module._cli_paths(
        Path(os.environ["HERMES_HOME"]), snapshot.handoff_id
    )
    displaced = paths.root.with_name("displaced")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "prompt.txt").write_text("attacker replacement", encoding="utf-8")
    (outside / "prompt.txt").chmod(0o600)

    with local_module._open_cli_dir(
        Path(os.environ["HERMES_HOME"]), snapshot.handoff_id
    ) as (_paths, directory_fd):
        paths.root.rename(displaced)
        paths.root.symlink_to(outside, target_is_directory=True)
        assert (
            local_module._read_bounded_at(directory_fd, "prompt.txt").decode("utf-8")
            == snapshot.spec.prompt
        )
        local_module._atomic_bytes_at(directory_fd, "stdout.txt", b"safe")

    assert (displaced / "stdout.txt").read_bytes() == b"safe"
    assert not (outside / "stdout.txt").exists()


def test_output_collectors_discard_bytes_above_the_bound():
    class _Process:
        returncode = 0
        stdout = io.BytesIO(b"x" * (local_module.MAX_CLI_OUTPUT_BYTES * 3))
        stderr = io.BytesIO(b"y" * (local_module.MAX_CLI_OUTPUT_BYTES * 2))

        def wait(self, timeout):
            return self.returncode

    returncode, stdout, stderr, timed_out = local_module._collect_process_output(
        _Process(), timeout_seconds=1
    )

    assert returncode == 0
    assert timed_out is False
    assert len(stdout) == local_module.MAX_CLI_OUTPUT_BYTES
    assert len(stderr) == local_module.MAX_CLI_OUTPUT_BYTES


def _assert_wrapper_timeout_terminates_the_destination_process_group(tmp_path):
    descendant_pid = tmp_path / "descendant.pid"
    script = (
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')\n"
        "time.sleep(60)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(descendant_pid)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    child_pid = None
    try:
        deadline = time.monotonic() + 5
        while not descendant_pid.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        child_pid = int(descendant_pid.read_text(encoding="utf-8"))
        assert os.getpgid(process.pid) == os.getpgrp()
        assert os.getpgid(child_pid) == os.getpgrp()

        _returncode, _stdout, _stderr, timed_out = (
            local_module._collect_process_output(
                process,
                timeout_seconds=0.05,
            )
        )

        assert timed_out is True
        deadline = time.monotonic() + 3
        while _pid_is_live(child_pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not _pid_is_live(child_pid)
    finally:
        if process.poll() is None:
            local_module.kill_process_tree(process.pid)
            process.wait(timeout=3)
        if child_pid is not None and _pid_is_live(child_pid):
            os.kill(child_pid, signal.SIGKILL)


@pytest.mark.macos_only
def test_macos_wrapper_timeout_terminates_the_destination_process_group(tmp_path):
    _assert_wrapper_timeout_terminates_the_destination_process_group(tmp_path)


@pytest.mark.linux_only
def test_linux_wrapper_timeout_terminates_the_destination_process_group(tmp_path):
    _assert_wrapper_timeout_terminates_the_destination_process_group(tmp_path)


def _pid_is_live(pid: int) -> bool:
    import psutil

    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def test_signal_exit_cannot_be_recorded_as_success(tmp_path, monkeypatch):
    channel, snapshot = _bound(tmp_path, monkeypatch)
    process = SimpleNamespace(
        returncode=-9,
        stdout=io.BytesIO(),
        stderr=io.BytesIO(),
        wait=lambda timeout: -9,
    )
    monkeypatch.setattr(
        local_module.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("unbounded run")),
    )
    monkeypatch.setattr(local_module.subprocess, "Popen", lambda *_a, **_k: process)

    local_module._run_cli_wrapper(snapshot.handoff_id, "reviewer")
    observed = channel.observe(snapshot, budget_seconds=1)
    assert observed.phase == "failed"
    assert observed.checkpoint["exit_code"] == 137


def test_wrapper_unlinks_prompt_and_never_persists_raw_stderr(
    tmp_path, monkeypatch
):
    _channel, snapshot = _bound(tmp_path, monkeypatch)
    secret_error = b"Authorization: Bearer provider-secret"
    process = SimpleNamespace(
        returncode=1,
        stdout=io.BytesIO(),
        stderr=io.BytesIO(secret_error),
        wait=lambda timeout: 1,
    )
    monkeypatch.setattr(local_module.subprocess, "Popen", lambda *_a, **_k: process)

    assert local_module._run_cli_wrapper(snapshot.handoff_id, "reviewer") == 1

    paths = local_module._cli_paths(
        Path(os.environ["HERMES_HOME"]), snapshot.handoff_id
    )
    receipt = json.loads(paths.receipt.read_text(encoding="utf-8"))
    assert not paths.prompt.exists()
    assert not paths.stderr.exists()
    assert secret_error.decode() not in json.dumps(receipt)
    assert receipt["stderr_size"] == len(secret_error)
    assert receipt["stderr_sha256"] == sha256(secret_error).hexdigest()


def test_submit_returns_process_identity_without_absolute_paths(tmp_path, monkeypatch):
    channel, snapshot = _bound(tmp_path, monkeypatch)
    identity = ProcessIdentity(pid=4321, start_time=9876, group_id=4321)

    def spawn(argv, **kwargs):
        assert kwargs["env"]["HERMES_HOME"] == os.environ["HERMES_HOME"]
        return SimpleNamespace(identity=identity)

    monkeypatch.setattr(
        local_module.ManagedProcessTree,
        "spawn",
        spawn,
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


def test_submit_strips_initiator_credentials_from_wrapper_environment(
    tmp_path, monkeypatch
):
    channel, snapshot = _bound(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "source-provider-canary")
    monkeypatch.setenv("API_SERVER_KEY", "source-api-canary")
    identity = ProcessIdentity(pid=4321, start_time=9876, group_id=4321)
    captured = {}

    def spawn(_argv, **kwargs):
        captured.update(kwargs["env"])
        return SimpleNamespace(identity=identity)

    monkeypatch.setattr(local_module.ManagedProcessTree, "spawn", spawn)

    channel.submit(snapshot, budget_seconds=1)

    assert captured["HERMES_HOME"] == os.environ["HERMES_HOME"]
    assert "OPENAI_API_KEY" not in captured
    assert "API_SERVER_KEY" not in captured


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


def test_service_cleans_cli_spool_only_after_receipt_is_durable(
    tmp_path, monkeypatch
):
    seed = _snapshot(tmp_path, monkeypatch)
    source_home = Path(os.environ["HERMES_HOME"])
    store = HandoffStore(source_home / "handoffs.db")
    channel = LocalHermesChannel(source_home)
    service = AgentHandoffService(store, channel)
    created = service.create(
        seed.spec,
        seed.key_scope,
        handoff_key=seed.handoff_key,
    )
    monkeypatch.setattr(
        channel,
        "_assess",
        lambda *_args: (None, "runs_not_durable"),
    )
    bound = service.advance(created.handoff_id).snapshot
    monkeypatch.setattr(
        channel,
        "_submit_cli",
        lambda snapshot: ChannelObservation(
            phase="submitted",
            checkpoint={
                **snapshot.checkpoint,
                "process_pid": 999999,
                "process_started_at": 1,
                "process_command_sha256": "a" * 64,
                "status": "running",
            },
        ),
    )
    submitted = service.advance(bound.handoff_id).snapshot
    paths, _receipt = _write_receipt(
        submitted,
        stdout=b"durable reply",
        stderr=b"provider-secret",
    )
    original_commit = store.commit_observation

    def commit_observation(lease, observation):
        committed = original_commit(lease, observation)
        if (observation.checkpoint or {}).get("receipt_sha256"):
            assert paths.stdout.exists()
            assert paths.receipt.exists()
        return committed

    monkeypatch.setattr(store, "commit_observation", commit_observation)

    terminal = service.advance(submitted.handoff_id).snapshot

    assert terminal.phase == "succeeded"
    assert terminal.terminal_result["text"] == "durable reply"
    assert not paths.root.exists()
    evidence = json.dumps(
        [dict(event.data) for event in service.evidence(terminal.handoff_id).events]
    )
    assert "durable reply" not in evidence
    assert "provider-secret" not in evidence


def test_cleanup_preserves_spool_until_lost_process_group_is_quiescent(
    tmp_path, monkeypatch
):
    channel, snapshot = _bound(tmp_path, monkeypatch)
    committed = replace(
        snapshot,
        phase="indeterminate",
        checkpoint={
            **snapshot.checkpoint,
            "process_pid": 123,
            "process_started_at": 456,
            "process_command_sha256": "a" * 64,
            "status": "running",
        },
        failure_code="local_cli_process_lost",
    )
    paths = local_module._cli_paths(
        Path(os.environ["HERMES_HOME"]), committed.handoff_id
    )
    monkeypatch.setattr(channel, "_identity_is_gone", lambda _identity: False)

    channel.cleanup_committed(committed)

    assert paths.prompt.exists()


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


def test_timeout_receipt_is_indeterminate(tmp_path, monkeypatch):
    channel, snapshot = _bound(tmp_path, monkeypatch)

    class _TimedOutProcess:
        returncode = None
        stdout = io.BytesIO(b"partial")
        stderr = io.BytesIO()

        def wait(self, timeout):
            raise subprocess.TimeoutExpired(["hermes"], timeout)

        def kill(self):
            return None

    monkeypatch.setattr(
        local_module.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("unbounded run")),
    )
    monkeypatch.setattr(
        local_module.subprocess, "Popen", lambda *_a, **_k: _TimedOutProcess()
    )
    local_module._run_cli_wrapper(snapshot.handoff_id, "reviewer")
    timeout = channel.observe(snapshot, budget_seconds=1)
    receipt = json.loads(
        local_module._cli_paths(
            Path(os.environ["HERMES_HOME"]), snapshot.handoff_id
        ).receipt.read_text()
    )
    assert receipt["outcome"] == "timeout"
    assert timeout.phase == "indeterminate"
    assert timeout.failure_code == "local_cli_timeout"


def test_committed_cli_receipt_fact_survives_spool_cleanup(tmp_path, monkeypatch):
    channel, snapshot = _bound(tmp_path, monkeypatch)
    checkpoint = {
        **snapshot.checkpoint,
        "receipt_version": local_module._CLI_RECEIPT_VERSION,
        "receipt_sha256": "a" * 64,
        "stdout_sha256": sha256(b"").hexdigest(),
        "stderr_sha256": sha256(b"provider-secret").hexdigest(),
        "exit_code": 124,
        "status": "timeout",
    }
    committed = replace(
        snapshot,
        phase="indeterminate",
        checkpoint=checkpoint,
        failure_code="local_cli_timeout",
    )

    observed = channel.reconcile(committed, budget_seconds=1)

    assert observed.phase == "indeterminate"
    assert observed.checkpoint == checkpoint
    assert observed.failure_code == "local_cli_timeout"


def test_cancel_requires_proven_identity_disappearance_after_terminate_true(
    tmp_path, monkeypatch
):
    channel, snapshot = _bound(tmp_path, monkeypatch)
    submitted = replace(snapshot, phase="cancelling", checkpoint={
        **snapshot.checkpoint,
        "process_pid": 123,
        "process_started_at": 456,
        "process_command_sha256": "a" * 64,
        "status": "running",
    })
    monkeypatch.setattr(channel, "_identity_is_current", lambda identity: True)
    monkeypatch.setattr(
        local_module.ManagedProcessTree, "terminate_existing", lambda identity: False
    )
    assert channel.cancel(submitted, budget_seconds=1).phase == "cancelling"
    monkeypatch.setattr(
        local_module.ManagedProcessTree, "terminate_existing", lambda identity: True
    )
    monkeypatch.setattr(channel, "_identity_is_gone", lambda identity: False)
    assert channel.cancel(submitted, budget_seconds=1).phase == "cancelling"
    monkeypatch.setattr(channel, "_identity_is_gone", lambda identity: True)
    assert channel.cancel(submitted, budget_seconds=1).phase == "cancelled"


def _assert_source_cancel_terminates_descendants_and_reaps_its_wrapper(
    tmp_path, monkeypatch
):
    channel, snapshot = _bound(tmp_path, monkeypatch)
    inner_pid_file = tmp_path / "cancel-inner.pid"
    grandchild_pid_file = tmp_path / "cancel-grandchild.pid"
    inner_script = (
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')\n"
        "time.sleep(60)\n"
    )
    script = (
        "import pathlib, subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', {inner_script!r}, sys.argv[2]])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')\n"
        "time.sleep(60)\n"
    )
    monkeypatch.setattr(
        local_module,
        "_wrapper_argv",
        lambda *_args: [
            sys.executable,
            "-c",
            script,
            str(inner_pid_file),
            str(grandchild_pid_file),
        ],
    )
    observation = channel.submit(snapshot, budget_seconds=1)
    submitted = replace(
        snapshot,
        phase="cancelling",
        checkpoint=observation.checkpoint,
    )
    wrapper_pid = observation.checkpoint["process_pid"]
    inner_pid = None
    grandchild_pid = None
    try:
        deadline = time.monotonic() + 5
        while (
            not inner_pid_file.exists() or not grandchild_pid_file.exists()
        ) and time.monotonic() < deadline:
            time.sleep(0.01)
        inner_pid = int(inner_pid_file.read_text(encoding="utf-8"))
        grandchild_pid = int(grandchild_pid_file.read_text(encoding="utf-8"))
        wrapper_group = os.getpgid(wrapper_pid)
        assert os.getpgid(inner_pid) == wrapper_group
        assert os.getpgid(grandchild_pid) == wrapper_group

        cancelled = channel.cancel(submitted, budget_seconds=1)

        assert cancelled.phase == "cancelled"
        assert not _pid_is_live(inner_pid)
        assert not _pid_is_live(grandchild_pid)
        with pytest.raises(ChildProcessError):
            os.waitpid(wrapper_pid, os.WNOHANG)
    finally:
        identity = channel._process_identity(submitted)
        if identity is not None:
            ManagedProcessTree.terminate_existing(identity)
        try:
            os.waitpid(wrapper_pid, os.WNOHANG)
        except ChildProcessError:
            pass
        for pid in (inner_pid, grandchild_pid):
            if pid is not None and _pid_is_live(pid):
                os.kill(pid, signal.SIGKILL)


@pytest.mark.macos_only
def test_macos_source_cancel_terminates_descendants_and_reaps_its_wrapper(
    tmp_path, monkeypatch
):
    _assert_source_cancel_terminates_descendants_and_reaps_its_wrapper(
        tmp_path, monkeypatch
    )


@pytest.mark.linux_only
def test_linux_source_cancel_terminates_descendants_and_reaps_its_wrapper(
    tmp_path, monkeypatch
):
    _assert_source_cancel_terminates_descendants_and_reaps_its_wrapper(
        tmp_path, monkeypatch
    )


def _assert_restart_reconcile_terminates_descendants_after_wrapper_exit(
    tmp_path, monkeypatch
):
    seed = _snapshot(tmp_path, monkeypatch)
    source_home = Path(os.environ["HERMES_HOME"])
    first_store = HandoffStore(source_home / "handoffs.db")
    first_channel = LocalHermesChannel(source_home)
    first_service = AgentHandoffService(first_store, first_channel)
    created = first_service.create(
        seed.spec,
        seed.key_scope,
        handoff_key=seed.handoff_key,
    )
    monkeypatch.setattr(
        first_channel,
        "_assess",
        lambda *_args: (None, "runs_not_durable"),
    )
    bound = first_service.advance(created.handoff_id).snapshot
    child_pid_file = tmp_path / "restart-child.pid"
    script = (
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')\n"
        "time.sleep(60)\n"
    )
    monkeypatch.setattr(
        local_module,
        "_wrapper_argv",
        lambda *_args: [sys.executable, "-c", script, str(child_pid_file)],
    )
    submitted = first_service.advance(bound.handoff_id).snapshot
    wrapper_pid = submitted.checkpoint["process_pid"]
    detached_tree = first_channel._take_cli_tree(submitted.handoff_id)
    assert detached_tree is not None
    child_pid = None
    second_store = None
    try:
        deadline = time.monotonic() + 5
        while not child_pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        assert os.getpgid(child_pid) == wrapper_pid

        os.kill(wrapper_pid, signal.SIGKILL)
        os.waitpid(wrapper_pid, 0)
        assert _pid_is_live(child_pid)
        first_store.close()

        second_store = HandoffStore(source_home / "handoffs.db")
        restarted = AgentHandoffService(
            second_store,
            LocalHermesChannel(source_home),
        )
        reconciled = restarted.advance(submitted.handoff_id).snapshot

        assert reconciled.phase == "indeterminate"
        deadline = time.monotonic() + 3
        while _pid_is_live(child_pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not _pid_is_live(child_pid)
    finally:
        if second_store is not None:
            second_store.close()
        else:
            first_store.close()
        if child_pid is not None and _pid_is_live(child_pid):
            try:
                os.killpg(wrapper_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.macos_only
@pytest.mark.live_system_guard_bypass
def test_macos_restart_reconcile_terminates_descendants_after_wrapper_exit(
    tmp_path, monkeypatch
):
    _assert_restart_reconcile_terminates_descendants_after_wrapper_exit(
        tmp_path, monkeypatch
    )


@pytest.mark.linux_only
@pytest.mark.live_system_guard_bypass
def test_linux_restart_reconcile_terminates_descendants_after_wrapper_exit(
    tmp_path, monkeypatch
):
    _assert_restart_reconcile_terminates_descendants_after_wrapper_exit(
        tmp_path, monkeypatch
    )


def test_identity_disappearance_requires_recorded_process_group_to_be_absent(
    monkeypatch,
):
    identity = ProcessIdentity(pid=123, start_time=456, group_id=123)
    monkeypatch.setattr("gateway.status._pid_exists", lambda pid: False)
    monkeypatch.setattr(local_module.os, "killpg", lambda group_id, signal: None)

    assert LocalHermesChannel._identity_is_gone(identity) is False

    def missing_group(group_id, signal):
        raise ProcessLookupError

    monkeypatch.setattr(local_module.os, "killpg", missing_group)
    assert LocalHermesChannel._identity_is_gone(identity) is True
