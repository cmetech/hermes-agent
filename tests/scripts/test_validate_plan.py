"""Behavioral tests for scripts/validate_plan.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = REPO_ROOT / "scripts" / "validate_plan.py"


def _run_validator(tmp_path: Path, plan: str) -> subprocess.CompletedProcess[str]:
    plan_path = tmp_path / "PLAN.md"
    plan_path.write_text(plan, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(plan_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_accepts_an_indented_dictionary_fragment(tmp_path):
    result = _run_validator(
        tmp_path,
        '''### Task 1: Config

```python
    # Inserted inside DEFAULT_CONFIG.
    "secret_keystore": "auto",
```
''',
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_rejects_a_symbol_defined_only_by_a_later_task(tmp_path):
    result = _run_validator(
        tmp_path,
        '''### Task 1: Consumer

```python
def consume():
    return _LATER
```

### Task 2: Producer

```python
_LATER = True
```
''',
    )

    assert result.returncode == 1
    assert "TASK-ORDER" in result.stdout
    assert "_LATER" in result.stdout


def test_rejects_load_env_claimed_as_the_startup_loader(tmp_path):
    result = _run_validator(
        tmp_path,
        '''### Task 1: Verification

```python
def test_startup_loading():
    from hermes_cli.config import load_env
    load_env()
```
''',
    )

    assert result.returncode == 1
    assert "STARTUP-LOADER" in result.stdout


def test_rejects_the_known_container_patch_seam_mismatch(tmp_path):
    result = _run_validator(
        tmp_path,
        '''### Task 2: File backend

```python
def test_container_refusal():
    from hermes_cli import config
    with mock.patch.object(config, "_is_container", return_value=True):
        FileKeystore(root).set("K", "v")
```

```python
def _in_container():
    return False

def create_key():
    if _in_container():
        raise KeystoreError
```
''',
    )

    assert result.returncode == 1
    assert "PATCH-SEAM" in result.stdout
