from __future__ import annotations

import json
import os
from pathlib import Path


artifacts = Path(os.environ["ARTIFACTS_DIR"])
artifacts.mkdir(parents=True, exist_ok=True)
checkpoint = {"schema_version": 1, "checkpoint": "deterministic-one-shot", "simulated": True}
(artifacts / "scheduled-checkpoint.json").write_text(
    json.dumps(checkpoint, sort_keys=True) + "\n", encoding="utf-8"
)
print(json.dumps(checkpoint, sort_keys=True))
