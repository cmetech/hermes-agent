"""Windows system locations survive the test runner's clean environment."""

from __future__ import annotations

import re
from pathlib import Path


RUN_TESTS = Path(__file__).resolve().parents[1] / "scripts" / "run_tests.sh"


def test_windows_system_drive_locations_are_forwarded() -> None:
    source = RUN_TESTS.read_text(encoding="utf-8")
    match = re.search(r"for _win_var in (?P<names>.*?); do", source, re.DOTALL)
    assert match is not None, "run_tests.sh lost its Windows env allowlist"

    names = match.group("names").split()
    assert "SYSTEMDRIVE" in names
    assert "ProgramData" in names
