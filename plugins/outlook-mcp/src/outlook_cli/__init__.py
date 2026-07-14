import subprocess
import os
import sys

SCRIPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "outlook-cli.ps1")
# Resolve to Windows path for powershell.exe
if SCRIPT_PATH.startswith("/mnt/"):
    parts = SCRIPT_PATH.split("/")
    SCRIPT_PATH = f"{parts[2].upper()}:\\" + "\\".join(parts[3:])


def run(*args: str) -> str:
    """Run outlook-cli.ps1 with given arguments and return stdout."""
    cmd = ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", SCRIPT_PATH]
    for a in args:
        if a.startswith("--"):
            cmd.append(f"-{a[2:]}")
        else:
            cmd.append(a)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0 and result.stderr:
        return f"Error: {result.stderr.strip()}"
    return result.stdout.strip()
