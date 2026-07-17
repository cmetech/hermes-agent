from __future__ import annotations

import json
import sys


for line in sys.stdin:
    request = json.loads(line)
    response = {"jsonrpc": "2.0", "id": request.get("id"), "result": request.get("params", {})}
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()
