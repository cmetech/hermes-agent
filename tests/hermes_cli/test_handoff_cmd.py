"""Behavioral tests for the profile-local ``hermes handoff`` CLI."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from hermes_cli.handoff import (
    ChannelObservation,
    HandoffEndpoint,
    HandoffSpec,
    HandoffStore,
)
from hermes_cli.handoff.cli import build_handoff_parser, cmd_handoff


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    build_handoff_parser(parser.add_subparsers(dest="command", required=True))
    return parser


def _spec(prompt: str = "private prompt: Authorization: Bearer secret-token") -> HandoffSpec:
    return HandoffSpec(
        mode="task",
        endpoint=HandoffEndpoint.parse("hermes://local/worker"),
        prompt=prompt,
        output_schema=None,
        deadline_at=None,
        attribution={"workflow_run": "run-1"},
        required_capabilities=frozenset(),
    )


def _create(home: Path, key: str, *, prompt: str | None = None):
    home.mkdir(parents=True, exist_ok=True)
    spec = _spec() if prompt is None else _spec(prompt)
    with HandoffStore(home / "handoffs.db") as store:
        return store.create_or_get("workflow", key, spec, spec.fingerprint)


def _create_peer(home: Path, key: str, *, phase: str):
    home.mkdir(parents=True, exist_ok=True)
    spec = HandoffSpec(
        mode="task",
        endpoint=HandoffEndpoint.parse("hermes://peer/spark/reviewer"),
        prompt="private remote prompt",
        output_schema=None,
        deadline_at=None,
        attribution={"workflow_run": "run-1"},
        required_capabilities=frozenset(),
    )
    with HandoffStore(home / "handoffs.db") as store:
        snapshot = store.create_or_get("workflow", key, spec, spec.fingerprint)
        snapshot = store.bind(
            snapshot.handoff_id,
            "peer_runs",
            {
                "peer": "spark",
                "profile": "reviewer",
                "mechanism": "peer_runs",
                "capabilities": [
                    "approval",
                    "authoritative_status",
                    "cancellation",
                    "durable_admission",
                    "follow_up",
                    "steering",
                ],
                "origin_sha256": "a" * 64,
                "auth_scope_sha256": "b" * 64,
            },
            {},
            snapshot.state_version,
        )
        lease = store.claim_advance(
            snapshot.handoff_id,
            "test-worker",
            now=datetime.now(timezone.utc),
            lease_seconds=10,
        )
        assert lease is not None
        store.journal_attempt(lease, "submit")
        checkpoint = {"run_id": "run-1", "status": "running"}
        if phase == "needs_input":
            checkpoint.update({
                "approval_request_id": "approval-1",
                "approval_choices": ["once", "deny"],
                "status": "waiting_for_approval",
            })
        snapshot = store.commit_observation(
            lease, ChannelObservation(phase=phase, checkpoint=checkpoint)
        )
        store.release_advance(lease, next_advance_at=None)
        return snapshot


def _run(argv: list[str]) -> tuple[int, argparse.Namespace]:
    args = _parser().parse_args(argv)
    return int(args.func(args) or 0), args


def test_parser_accepts_every_operator_command():
    cases = {
        ("list", "--phase", "active", "--limit", "7", "--json"): "list",
        ("show", "handoff-1", "--json"): "show",
        ("evidence", "handoff-1", "--after", "4", "--limit", "8", "--json"): "evidence",
        ("reconcile", "handoff-1", "--command-id", "cmd-1", "--json"): "reconcile",
        ("cancel", "handoff-1", "--command-id", "cmd-2", "--json"): "cancel",
        (
            "respond",
            "handoff-1",
            "--request-id",
            "approval-1",
            "--choice",
            "once",
            "--command-id",
            "cmd-3",
            "--json",
        ): "respond",
        (
            "steer",
            "handoff-1",
            "tighten",
            "the",
            "ending",
            "--command-id",
            "cmd-4",
            "--json",
        ): "steer",
        (
            "message",
            "handoff-1",
            "check",
            "again",
            "--correlation-id",
            "follow-up-1",
            "--command-id",
            "cmd-5",
            "--json",
        ): "message",
        ("advance", "handoff-1", "--budget-seconds", "0.5", "--json"): "advance",
    }

    for argv, action in cases.items():
        args = _parser().parse_args(("handoff", *argv))
        assert args.handoff_action == action
        assert args.func is cmd_handoff


def test_list_filters_phase_and_uses_selected_home(monkeypatch, tmp_path, capsys):
    first_home = tmp_path / "first"
    second_home = tmp_path / "second"
    first = _create(first_home, "first")
    second = _create(second_home, "second")
    with HandoffStore(second_home / "handoffs.db") as store:
        store.record_command(second.handoff_id, "cancel-1", "cancel", {"actor": "operator"})

    monkeypatch.setenv("HERMES_HOME", str(second_home))
    rc, _ = _run(["handoff", "list", "--phase", "cancelling", "--json"])

    assert rc == 0
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert [item["handoff_id"] for item in payload["handoffs"]] == [second.handoff_id]
    assert first.handoff_id not in raw


def test_real_startup_applies_profile_before_handoff_store(monkeypatch, tmp_path):
    default_home = tmp_path / ".hermes"
    profile_home = default_home / "profiles" / "operator"
    selected = _create(profile_home, "selected", prompt="selected profile prompt")
    _create(default_home, "default", prompt="default profile prompt")
    env = os.environ.copy()
    env.update({"HOME": str(tmp_path), "HERMES_HOME": str(default_home)})

    completed = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "-p", "operator", "handoff", "list", "--json"],
        cwd=Path(__file__).parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert [item["handoff_id"] for item in payload["handoffs"]] == [selected.handoff_id]


def test_json_show_is_diagnostic_and_never_discloses_prompt_or_result_text(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    snapshot = _create(tmp_path, "safe-json")
    result_text = "private result: password=another-secret"
    encoded = result_text.encode()
    with HandoffStore(tmp_path / "handoffs.db") as store:
        store.bind(
            snapshot.handoff_id,
            "local_runs",
            {"profile": "worker", "mechanism": "local_runs"},
            {},
            snapshot.state_version,
        )
        lease = store.claim_advance(
            snapshot.handoff_id,
            "test-worker",
            now=datetime.now(timezone.utc),
            lease_seconds=10,
        )
        assert lease is not None
        store.journal_attempt(lease, "submit")
        store.commit_observation(
            lease,
            ChannelObservation(
                phase="succeeded",
                terminal_result={
                    "text": result_text,
                    "sha256": sha256(encoded).hexdigest(),
                    "media_type": "text/plain",
                    "size_bytes": len(encoded),
                },
            ),
        )

    rc, _ = _run(["handoff", "show", snapshot.handoff_id, "--json"])

    assert rc == 0
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert payload["handoff_id"] == snapshot.handoff_id
    assert payload["endpoint"] == "hermes://local/worker"
    assert payload["phase"] == "succeeded"
    assert payload["terminal_summary"]["size_bytes"] == len(encoded)
    assert "prompt" not in json.dumps(payload).lower()
    assert "secret-token" not in raw
    assert "another-secret" not in raw
    assert "authorization" not in raw.lower()


def test_text_show_contains_required_safe_diagnostics(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    snapshot = _create(tmp_path, "text")

    rc, _ = _run(["handoff", "show", snapshot.handoff_id])

    assert rc == 0
    output = capsys.readouterr().out
    for label in (
        "handoff_id:",
        "endpoint:",
        "mechanism:",
        "phase:",
        "age:",
        "next_observation:",
        "terminal_summary:",
        "failure_code:",
    ):
        assert label in output
    assert "secret-token" not in output


def test_evidence_is_paginated_and_exposes_only_redacted_store_data(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    snapshot = _create(tmp_path, "events")
    with HandoffStore(tmp_path / "handoffs.db") as store:
        store.record_command(
            snapshot.handoff_id, "reconcile-1", "reconcile", {"actor": "operator"}
        )

    rc, _ = _run(
        ["handoff", "evidence", snapshot.handoff_id, "--limit", "1", "--json"]
    )

    assert rc == 0
    first = json.loads(capsys.readouterr().out)
    assert len(first["events"]) == 1
    assert first["has_more"] is True
    assert first["next_after_sequence"] == 1
    rc, _ = _run(
        [
            "handoff",
            "evidence",
            snapshot.handoff_id,
            "--after",
            str(first["next_after_sequence"]),
            "--limit",
            "1",
            "--json",
        ]
    )
    second = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert [event["kind"] for event in second["events"]] == ["reconcile_requested"]
    assert "secret-token" not in json.dumps((first, second))


def test_text_evidence_starts_with_safe_handoff_diagnostics(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    snapshot = _create(tmp_path, "text-evidence")

    rc, _ = _run(["handoff", "evidence", snapshot.handoff_id])

    assert rc == 0
    output = capsys.readouterr().out
    for label in (
        "handoff_id:",
        "endpoint:",
        "mechanism:",
        "phase:",
        "age:",
        "next_observation:",
        "terminal_summary:",
        "failure_code:",
    ):
        assert label in output
    assert output.index("handoff_id:") < output.index("1\t")
    assert "secret-token" not in output


def test_empty_json_evidence_page_retains_safe_handoff_identity(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    snapshot = _create(tmp_path, "empty-evidence")

    rc, _ = _run(
        ["handoff", "evidence", snapshot.handoff_id, "--after", "99", "--json"]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["handoff_id"] == snapshot.handoff_id
    assert payload["endpoint"] == "hermes://local/worker"
    assert payload["events"] == []
    assert payload["next_after_sequence"] == 99
    assert "prompt" not in json.dumps(payload).lower()
    assert "secret-token" not in json.dumps(payload)


@pytest.mark.parametrize("action", ["reconcile", "cancel"])
def test_mutation_command_id_is_printed_and_replay_is_idempotent(
    action, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    snapshot = _create(tmp_path, action)
    argv = [
        "handoff",
        action,
        snapshot.handoff_id,
        "--command-id",
        "operator-command",
        "--json",
    ]

    assert _run(argv)[0] == 0
    first = json.loads(capsys.readouterr().out)
    assert _run(argv)[0] == 0
    second = json.loads(capsys.readouterr().out)

    assert first["command_id"] == second["command_id"] == "operator-command"
    with HandoffStore(tmp_path / "handoffs.db") as store:
        evidence = store.evidence(snapshot.handoff_id, after_sequence=0, limit=100)
    assert [event.kind for event in evidence.events].count(f"{action}_requested") == 1


def test_mutation_generates_and_reports_command_id(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    snapshot = _create(tmp_path, "generated-command")

    rc, _ = _run(["handoff", "reconcile", snapshot.handoff_id, "--json"])

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["command_id"].startswith("operator-")


def test_respond_records_exact_private_request_without_printing_it(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    snapshot = _create_peer(tmp_path, "respond", phase="needs_input")

    rc, _ = _run([
        "handoff",
        "respond",
        snapshot.handoff_id,
        "--request-id",
        "approval-1",
        "--choice",
        "once",
        "--command-id",
        "respond-1",
        "--json",
    ])

    assert rc == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload == {
        "command_id": "respond-1",
        "command_kind": "respond",
        "delivery_state": "pending",
        "failure_code": None,
    }
    assert "approval-1" not in output
    with HandoffStore(tmp_path / "handoffs.db") as store:
        assert dict(store.get_command(snapshot.handoff_id, "respond-1").payload) == {
            "actor": "operator",
            "choice": "once",
            "request_id": "approval-1",
        }


@pytest.mark.parametrize(
    ("action", "extra", "expected_payload"),
    [
        (
            "steer",
            [],
            {"actor": "operator", "text": "Tighten the ending."},
        ),
        (
            "message",
            ["--correlation-id", "follow-up-1"],
            {
                "actor": "operator",
                "correlation_id": "follow-up-1",
                "text": "Tighten the ending.",
            },
        ),
    ],
)
def test_guidance_commands_record_distinct_bounded_payloads_without_echoing_text(
    action, extra, expected_payload, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    snapshot = _create_peer(tmp_path, action, phase="active")
    command_id = f"{action}-1"

    rc, _ = _run([
        "handoff",
        action,
        snapshot.handoff_id,
        "Tighten",
        "the",
        "ending.",
        *extra,
        "--command-id",
        command_id,
        "--json",
    ])

    assert rc == 0
    output = capsys.readouterr().out
    assert "Tighten" not in output
    assert "follow-up-1" not in output
    assert json.loads(output)["command_kind"] == action
    with HandoffStore(tmp_path / "handoffs.db") as store:
        command = store.get_command(snapshot.handoff_id, command_id)
    assert command.kind == action
    assert dict(command.payload) == expected_payload


def test_control_command_replay_is_idempotent_and_conflict_is_closed(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    snapshot = _create_peer(tmp_path, "replay", phase="active")
    base = [
        "handoff",
        "steer",
        snapshot.handoff_id,
        "first",
        "--command-id",
        "steer-1",
        "--json",
    ]
    assert _run(base)[0] == 0
    capsys.readouterr()
    assert _run(base)[0] == 0
    capsys.readouterr()
    changed = [*base]
    changed[3] = "changed"
    assert _run(changed)[0] == 1
    assert "handoff_conflict" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["handoff", "respond", "missing", "--request-id", "approval-1", "--choice", "once"],
        ["handoff", "steer", "missing", "text"],
        ["handoff", "message", "missing", "text", "--correlation-id", "follow-up-1"],
    ],
)
def test_control_commands_reject_unknown_handoff_before_recording(
    argv, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert _run(argv)[0] == 1
    assert "handoff_not_found" in capsys.readouterr().err


def test_control_commands_reject_local_fallback_wrong_phase_and_oversized_text(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    local = _create(tmp_path, "local")
    active = _create_peer(tmp_path, "active-control", phase="active")
    waiting = _create_peer(tmp_path, "waiting-control", phase="needs_input")

    cases = [
        ["handoff", "steer", local.handoff_id, "text"],
        [
            "handoff",
            "respond",
            active.handoff_id,
            "--request-id",
            "approval-1",
            "--choice",
            "once",
        ],
        ["handoff", "steer", waiting.handoff_id, "text"],
        ["handoff", "steer", active.handoff_id, "x" * 16_385],
    ]
    for argv in cases:
        assert _run(argv)[0] == 2
        assert "invalid_argument" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "expected_code"),
    [
        (["handoff", "show", "missing"], "handoff_not_found"),
        (["handoff", "evidence", "missing", "--after", "0"], "handoff_not_found"),
        (["handoff", "advance", "missing"], "handoff_not_found"),
        (["handoff", "advance", "missing", "--budget-seconds", "0"], "invalid_argument"),
        (["handoff", "list", "--limit", "0"], "invalid_argument"),
    ],
)
def test_errors_are_stable_nonzero_and_do_not_echo_unsafe_details(
    argv, expected_code, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    rc, _ = _run(argv)

    captured = capsys.readouterr()
    assert rc != 0
    assert expected_code in captured.err
    assert "traceback" not in captured.err.lower()


def test_parser_rejects_invalid_phase_before_store_access():
    with pytest.raises(SystemExit) as exc:
        _parser().parse_args(["handoff", "list", "--phase", "not-a-phase"])
    assert exc.value.code == 2


@pytest.mark.parametrize("budget", ["0", "-1", "nan", "inf"])
def test_advance_rejects_nonpositive_or_nonfinite_budgets(
    budget, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    snapshot = _create(tmp_path, f"budget-{budget}")

    rc, _ = _run(
        ["handoff", "advance", snapshot.handoff_id, "--budget-seconds", budget]
    )

    assert rc == 2
    assert "invalid_argument" in capsys.readouterr().err


def test_handler_does_not_render_untrusted_exception_text(monkeypatch, capsys):
    class UnsafeService:
        def get(self, _handoff_id):
            raise RuntimeError("Authorization: Bearer leaked-secret")

    monkeypatch.setattr("hermes_cli.handoff.cli._service", lambda: UnsafeService())

    rc = cmd_handoff(SimpleNamespace(handoff_action="show", handoff_id="x", json=False))

    assert rc == 1
    error = capsys.readouterr().err
    assert "handoff_internal_error" in error
    assert "leaked-secret" not in error


def test_mutation_constructor_failure_is_redacted_and_reports_generated_command_id(
    monkeypatch, capsys
):
    def fail_constructor():
        raise RuntimeError("Authorization: Bearer constructor-secret")

    monkeypatch.setattr("hermes_cli.handoff.cli._service", fail_constructor)

    rc = cmd_handoff(
        SimpleNamespace(
            handoff_action="cancel",
            handoff_id="x",
            command_id=None,
            json=True,
        )
    )

    assert rc == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["code"] == "handoff_internal_error"
    assert payload["command_id"].startswith("operator-")
    assert "constructor-secret" not in json.dumps(payload)


@pytest.mark.parametrize("action", ["list", "show", "evidence"])
def test_read_constructor_failures_return_stable_redacted_error(
    action, monkeypatch, capsys
):
    def fail_constructor():
        raise RuntimeError("password=constructor-secret")

    monkeypatch.setattr("hermes_cli.handoff.cli._service", fail_constructor)

    rc = cmd_handoff(SimpleNamespace(handoff_action=action, json=False))

    assert rc == 1
    error = capsys.readouterr().err
    assert "handoff_internal_error" in error
    assert "constructor-secret" not in error
