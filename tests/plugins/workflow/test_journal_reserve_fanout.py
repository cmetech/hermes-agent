"""A live sibling's reserve snapshot must not cap the rest of a fan-out.

Every attempt reserves durable journal capacity sized from the projection as it
stood when that attempt claimed. In a parallel fan-out all siblings claim while
the projection is still small, then each completing sibling appends its results
and grows it. Validating a later sibling's terminal write against the oldest
live sibling's frozen snapshot fails a healthy run with executor_crash -- seen
on macos-latest, and measured at only ~9% headroom (1224 bytes of 13258) on the
five-wide laptop-diagnostic fan-out even where it passed.

The reserve exists to guarantee an attempt durable room for its own terminal and
recovery evidence. When the projection outgrows the snapshot, the correct
response is to re-reserve at the current size and let the journal quota decide,
not to fail the run -- so `event_journal_quota` always means "out of durable
space", never "an older sibling's snapshot went stale".
"""
from __future__ import annotations

import json

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore, StorageQuotaError


def _start(store, package, key):
    prepared = store.prepare_run_snapshot(package)
    return store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key=key,
            concurrency_key=package.definition.name,
            concurrency_policy="allow",
        ),
        immutable_snapshot=prepared,
    )


def _fanout(tmp_path, workflow_writer, width, **store_kwargs):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name=f"fanout{width}",
            nodes=[{"id": f"n{i}", "bash": "true"} for i in range(width)],
        )
    )
    store = RunStore(tmp_path / "home", max_total_workers=width, **store_kwargs)
    admitted = _start(store, package, f"fanout-{width}")
    claims = []
    for i in range(width):
        claim = store.claim_node(
            admitted.run_id, f"n{i}", f"worker-{i}", max_run_workers=width
        )
        assert claim is not None, f"n{i} could not claim a worker slot"
        store.mark_node_started(claim)
        claims.append(claim)
    return store, admitted.run_id, claims


def _reserve_rows(store, run_id):
    with store._connect() as connection:
        return {
            str(row["attempt_id"]): (
                int(row["projection_limit_bytes"]),
                int(row["terminal_reserve_bytes"]),
            )
            for row in connection.execute(
                "SELECT attempt_id, projection_limit_bytes, terminal_reserve_bytes "
                "FROM attempt_journal_reserves WHERE run_id=?",
                (run_id,),
            ).fetchall()
        }


def _grown_projection(store, run_id, ceiling):
    """A projection that has legitimately outgrown a sibling's snapshot."""
    projection = store.load_run(run_id)
    padding = "x" * (ceiling * 2)
    projection["nodes"]["n0"]["grown_evidence"] = padding
    assert (
        len(json.dumps(projection, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        > ceiling
    )
    return projection


def test_growth_past_a_live_siblings_snapshot_re_reserves_instead_of_failing(
    tmp_path, workflow_writer
) -> None:
    store, run_id, claims = _fanout(tmp_path, workflow_writer, 4)
    before = _reserve_rows(store, run_id)
    stale_attempt = claims[0].attempt_id
    terminal_attempt = claims[-1].attempt_id
    stale_ceiling = before[stale_attempt][0]
    projection = _grown_projection(store, run_id, stale_ceiling)

    # Previously raised StorageQuotaError("event_journal_quota protected
    # terminal projection reserve") and failed the run with executor_crash.
    store._check_journal_reserve(
        run_id=run_id,
        projection=projection,
        journal_bytes=0,
        frame_bytes=512,
        terminal_attempt_id=terminal_attempt,
        connection=None,
    )

    after = _reserve_rows(store, run_id)
    assert after[stale_attempt][0] > stale_ceiling, "stale ceiling was not re-reserved"
    assert after[stale_attempt][1] > before[stale_attempt][1], "reserve did not grow"
    # The terminal attempt is accounted for by frame consumption, not re-reserve.
    assert after[terminal_attempt] == before[terminal_attempt]


def test_re_reserve_still_refuses_when_the_journal_is_genuinely_exhausted(
    tmp_path, workflow_writer
) -> None:
    """Re-reserving must never become a way to exceed max_journal_bytes."""
    store, run_id, claims = _fanout(tmp_path, workflow_writer, 4)
    store.max_journal_bytes = 16 * 1024
    before = _reserve_rows(store, run_id)
    stale_attempt = claims[0].attempt_id
    projection = _grown_projection(store, run_id, before[stale_attempt][0])

    with pytest.raises(StorageQuotaError) as excinfo:
        store._check_journal_reserve(
            run_id=run_id,
            projection=projection,
            journal_bytes=0,
            frame_bytes=512,
            terminal_attempt_id=claims[-1].attempt_id,
            connection=None,
        )

    # The honest capacity error, not the stale-snapshot one.
    assert "protected terminal recovery capacity" in str(excinfo.value)
    # A refused write must not leave a widened reserve behind.
    assert _reserve_rows(store, run_id) == before


@pytest.mark.parametrize("width", [4, 8, 16])
def test_wide_fanout_runs_to_completion(tmp_path, workflow_writer, width) -> None:
    store, run_id, claims = _fanout(tmp_path, workflow_writer, width)

    for i, claim in enumerate(claims):
        store.complete_node(claim, status="succeeded")
        assert store.load_run(run_id)["nodes"][f"n{i}"]["state"] == "succeeded"

    projection = store.load_run(run_id)
    assert projection["last_error"] is None
    assert all(node["state"] == "succeeded" for node in projection["nodes"].values())
