"""uv subprocesses must never inherit Python stdlib redirection.

The scrub used to be gated on ``_is_termux_env``. It is not a Termux problem:
a managed corporate Windows baseline setting ``PYTHONHOME`` at Machine scope
killed the ``dependencies`` install stage with ``AssertionError: SRE module
mismatch``, because ``PYTHONHOME`` overrides an interpreter's own stdlib
location and uv's isolated build backend inherits whatever we leave set.

Regression shape: assert the scrub happens on a NON-Termux environment, which
is the case the old gate skipped.
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

from hermes_cli.config import INHERITED_PYTHON_ENV_VARS
from hermes_cli.main import _uv_subprocess_env


POISONED = {
    "PYTHONHOME": r"C:\Python\Python310",
    "PYTHONPATH": r"C:\corp\lib;C:\corp\other",
    "PYTHONSTARTUP": "/etc/pythonstart.py",
    "PYTHONEXECUTABLE": r"C:\Python\Python310\python.exe",
    "PYTHONUSERBASE": r"C:\corp\site",
}


@pytest.fixture
def poisoned_environ():
    with mock.patch.dict(os.environ, {**POISONED, "UNRELATED_VAR": "keep-me"}, clear=False):
        yield


def test_uv_env_strips_inherited_python_vars_outside_termux(poisoned_environ) -> None:
    with mock.patch("hermes_cli.main._is_termux_env", return_value=False):
        env = _uv_subprocess_env()

    for name in INHERITED_PYTHON_ENV_VARS:
        assert name not in env, f"{name} would reach uv and its build backend"


def test_uv_env_disables_user_site_and_keeps_the_venv_pin(poisoned_environ) -> None:
    env = _uv_subprocess_env()

    assert env["PYTHONNOUSERSITE"] == "1"
    # The reason this env exists at all: point uv at the project venv.
    assert env["VIRTUAL_ENV"].endswith("venv")
    # Scrubbing is narrow -- unrelated inherited variables still pass through.
    assert env["UNRELATED_VAR"] == "keep-me"


def test_uv_env_does_not_mutate_the_process_environment(poisoned_environ) -> None:
    _uv_subprocess_env()

    # The scrub applies to the child only; mutating os.environ here would change
    # the behaviour of the running CLI itself.
    assert os.environ["PYTHONHOME"] == POISONED["PYTHONHOME"]
