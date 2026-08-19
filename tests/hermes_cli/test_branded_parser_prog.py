"""Behavioral contracts for branded top-level argparse identity."""

from __future__ import annotations

import sys

import pytest


def test_loop24_windows_entrypoint_names_usage_and_errors_loop24(
    monkeypatch, capsys
):
    from hermes_cli._parser import build_top_level_parser

    monkeypatch.setenv("OTTO_BRAND", "loop24")
    monkeypatch.setattr(
        sys,
        "argv",
        [r"C:\Users\splunk\AppData\Local\Programs\LOOP24\loop24.exe"],
    )

    parser, _subparsers, _chat_parser = build_top_level_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["issue"])

    assert exc_info.value.code == 2
    stderr = capsys.readouterr().err
    assert stderr.startswith("usage: loop24 ")
    assert "\nloop24: error: argument command: invalid choice: 'issue'" in stderr


def test_loop24_root_help_lists_bundled_plugin_commands(
    monkeypatch, capsys, tmp_path
):
    from hermes_cli.main import main

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "loop24-home"))
    monkeypatch.setenv("OTTO_BRAND", "loop24")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            r"C:\Users\splunk\AppData\Local\Programs\LOOP24\loop24.exe",
            "--help",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 0
    stdout = capsys.readouterr().out
    for command in ("jira", "gitlab", "confluence", "arm", "workflow"):
        assert f"    {command}" in stdout
    assert "LOOP24 Coworker - AI assistant with tool-calling capabilities" in stdout
    assert "Hermes Agent" not in stdout
    assert '    loop24 chat -q "Hello"' in stdout
    assert "    loop24 -s hermes-agent-dev,github-auth" in stdout
    assert "    loop24 <command> --help" in stdout
    assert "\n    hermes " not in stdout
    assert "Hermes " not in stdout
    assert "`hermes " not in stdout
    assert "HERMES_INFERENCE_MODEL" in stdout
    assert "hermes-agent-dev" in stdout
