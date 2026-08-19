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
