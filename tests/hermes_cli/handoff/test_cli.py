from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

from hermes_cli.handoff.models import HandoffEndpoint, HandoffSnapshot, HandoffSpec
from hermes_cli.handoff.projection import snapshot_summary


def test_shared_snapshot_projection_is_bounded_and_redacted():
    private = "Authorization: Bearer private-result"
    encoded = private.encode()
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    spec = HandoffSpec(
        mode="task",
        endpoint=HandoffEndpoint.parse("hermes://local/reviewer"),
        prompt="private prompt",
        output_schema=None,
        deadline_at=None,
        attribution={"workflow": "release"},
        required_capabilities=frozenset(),
    )
    snapshot = HandoffSnapshot(
        handoff_id="handoff-1",
        key_scope="workflow/run-1",
        handoff_key="review",
        spec=spec,
        spec_fingerprint=spec.fingerprint,
        phase="succeeded",
        state_version=2,
        mechanism="local_runs",
        binding=None,
        checkpoint=None,
        next_advance_at=None,
        submit_attempted_at=now,
        cancel_requested_at=None,
        terminal_result={
            "text": private,
            "sha256": sha256(encoded).hexdigest(),
            "media_type": "text/plain",
            "size_bytes": len(encoded),
        },
        failure_code=None,
        created_at=now,
        updated_at=now,
    )

    projected = snapshot_summary(snapshot, now=now)
    raw = json.dumps(projected)

    assert projected["terminal_summary"] == {
        "media_type": "text/plain",
        "sha256": sha256(encoded).hexdigest(),
        "size_bytes": len(encoded),
    }
    assert "private prompt" not in raw
    assert "private-result" not in raw
    assert "Authorization" not in raw
