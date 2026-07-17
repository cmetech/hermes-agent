from __future__ import annotations

import json
import os
import subprocess
import sys
import time


mode = os.environ.get("ARGUMENTS", "timeout").strip()
seconds = 4 if mode == "timeout" else 10
child = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({seconds})"])
try:
    deadline = time.monotonic() + seconds
    while child.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
finally:
    if child.poll() is None:
        child.terminate()
        child.wait(timeout=2)
print(json.dumps({"mode": mode, "bounded": True}))
