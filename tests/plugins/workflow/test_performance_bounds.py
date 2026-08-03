from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from hermes_cli.plugin_services import BackgroundServiceContext
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.bash_rendering import (
    BashRenderingError,
    classify_bash_reference_spans,
)
from plugins.workflow.coordinator import WorkflowCoordinatorService
from plugins.workflow.coordinator_store import (
    CoordinatorIdentity,
    CoordinatorStore,
    record_coordinator_wake,
)
from plugins.workflow.lease_clock import LeaseClockSample
from plugins.workflow.language_schema import (
    iter_output_reference_candidate_spans,
    iter_when_output_references,
)
from plugins.workflow.models import ExecutionFence
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
from plugins.workflow.topology import project_topology


class _SliceAccountingText(str):
    """A string that records copied slice volume across derived slices."""

    def __new__(
        cls, value: str, account: dict[str, int] | None = None
    ) -> _SliceAccountingText:
        instance = super().__new__(cls, value)
        instance.account = account if account is not None else {"characters": 0}
        return instance

    def __getitem__(self, key):
        value = super().__getitem__(key)
        if isinstance(key, slice):
            start, stop, step = key.indices(len(self))
            if step == 1:
                self.account["characters"] += max(0, stop - start)
            if isinstance(value, str):
                return type(self)(value, self.account)
        return value


class _IndexAccountingText(str):
    """A string that records direct character reads during candidate scans."""

    def __new__(cls, value: str) -> _IndexAccountingText:
        instance = super().__new__(cls, value)
        instance.index_reads = 0
        return instance

    def __getitem__(self, key):
        if isinstance(key, int):
            self.index_reads += 1
        return super().__getitem__(key)


def _condition_reference_slice_volume(clauses: int) -> tuple[int, int, int]:
    expression = _SliceAccountingText(
        " && ".join(
            f"$producer.output.field == {index}" for index in range(clauses)
        )
    )

    references = tuple(
        iter_when_output_references(expression, normalizer_version=3)
    )

    assert len(references) == clauses
    assert references[0].start == 0
    assert references[-1].end == str(expression).rfind(" == ")
    return len(expression), expression.account["characters"], len(references)


def test_many_clause_condition_reference_discovery_copies_only_linear_bytes() -> None:
    small_bytes, small_slices, _ = _condition_reference_slice_volume(512)
    large_bytes, large_slices, count = _condition_reference_slice_volume(1024)

    assert count == 1024
    assert large_bytes > small_bytes
    assert large_slices <= (3 * small_slices) + large_bytes
    assert large_slices <= 4 * large_bytes


def test_dollar_dense_bash_candidate_discovery_reads_only_linear_characters() -> None:
    small = _IndexAccountingText("$" * 16_384)
    large = _IndexAccountingText("$" * 32_768)

    assert (
        tuple(iter_output_reference_candidate_spans(small, normalizer_version=3)) == ()
    )
    assert (
        tuple(iter_output_reference_candidate_spans(large, normalizer_version=3)) == ()
    )

    assert large.index_reads <= (3 * small.index_reads) + len(large)
    assert large.index_reads <= 4 * len(large)


def _continued_bash_lexer_reads(repetitions: int) -> tuple[int, int]:
    prefix = "word\\\n" * repetitions
    suffix = "(\\\n( $USER_MESSAGE ))"
    template = _IndexAccountingText(prefix + suffix)
    start = len(prefix) + suffix.index("$USER_MESSAGE")

    with pytest.raises(BashRenderingError) as exc:
        classify_bash_reference_spans(
            template,
            ((start, start + len("$USER_MESSAGE")),),
        )

    assert exc.value.code == "bash_reference_context_unsupported"
    return len(template), template.index_reads


def test_line_continuation_logical_bash_lexing_reads_only_linear_characters() -> None:
    small_bytes, small_reads = _continued_bash_lexer_reads(4_096)
    large_bytes, large_reads = _continued_bash_lexer_reads(8_192)

    assert large_bytes > small_bytes
    assert large_reads <= (3 * small_reads) + large_bytes
    assert large_reads <= 6 * large_bytes


def _quoted_heredoc_lexer_reads(repetitions: int) -> tuple[int, int]:
    prefix = "cat <<'EOF' >/dev/null\n" + "line\\\n" * repetitions + "EOF\n"
    suffix = 'printf \'%s\' "$USER_MESSAGE"'
    template = _IndexAccountingText(prefix + suffix)
    start = len(prefix) + suffix.index("$USER_MESSAGE")

    assert classify_bash_reference_spans(
        template,
        ((start, start + len("$USER_MESSAGE")),),
    ) == ((start, start + len("$USER_MESSAGE"), '"'),)
    return len(template), template.index_reads


def test_quoted_heredoc_literal_continuations_are_classified_in_linear_time() -> None:
    small_bytes, small_reads = _quoted_heredoc_lexer_reads(4_096)
    large_bytes, large_reads = _quoted_heredoc_lexer_reads(8_192)

    assert large_bytes > small_bytes
    assert large_reads <= (3 * small_reads) + large_bytes
    assert large_reads <= 8 * large_bytes


def _physical_comment_lexer_reads(repetitions: int) -> tuple[int, int]:
    prefix = "# physical comment \\\n" * repetitions
    suffix = 'printf \'%s\' "$USER_MESSAGE"'
    template = _IndexAccountingText(prefix + suffix)
    start = len(prefix) + suffix.index("$USER_MESSAGE")

    assert classify_bash_reference_spans(
        template,
        ((start, start + len("$USER_MESSAGE")),),
    ) == ((start, start + len("$USER_MESSAGE"), '"'),)
    return len(template), template.index_reads


def test_physical_comment_newlines_are_classified_in_linear_time() -> None:
    small_bytes, small_reads = _physical_comment_lexer_reads(4_096)
    large_bytes, large_reads = _physical_comment_lexer_reads(8_192)

    assert large_bytes > small_bytes
    assert large_reads <= (3 * small_reads) + large_bytes
    assert large_reads <= 8 * large_bytes


def _joined_strip_heredoc_lexer_reads(repetitions: int) -> tuple[int, int]:
    prefix = (
        ": 3<\\\n<-\\\n'ONE' 4<<-\\\n\"TWO\"\n"
        + "\tone\\\n" * repetitions
        + "\tONE\n"
        + "\ttwo\\\n" * repetitions
        + "\tTWO\n"
    )
    suffix = 'printf \'%s\' "$USER_MESSAGE"'
    template = _IndexAccountingText(prefix + suffix)
    start = len(prefix) + suffix.index("$USER_MESSAGE")

    assert classify_bash_reference_spans(
        template,
        ((start, start + len("$USER_MESSAGE")),),
    ) == ((start, start + len("$USER_MESSAGE"), '"'),)
    return len(template), template.index_reads


def test_joined_multiple_strip_heredocs_are_classified_in_linear_time() -> None:
    small_bytes, small_reads = _joined_strip_heredoc_lexer_reads(2_048)
    large_bytes, large_reads = _joined_strip_heredoc_lexer_reads(4_096)

    assert large_bytes > small_bytes
    assert large_reads <= (3 * small_reads) + large_bytes
    assert large_reads <= 10 * large_bytes


def _phase_reentry_lexer_reads(repetitions: int) -> tuple[int, int]:
    prefix = (
        "(( shifted = 1 << 2 )); " * repetitions
        + ": \\\n# comment \\\n" * repetitions
        + "cat <<'EOF' >/dev/null\nline\\\nEOF\n"
    )
    suffix = 'printf \'%s\' "$USER_MESSAGE"'
    template = _IndexAccountingText(prefix + suffix)
    start = len(prefix) + suffix.index("$USER_MESSAGE")

    assert classify_bash_reference_spans(
        template,
        ((start, start + len("$USER_MESSAGE")),),
    ) == ((start, start + len("$USER_MESSAGE"), '"'),)
    return len(template), template.index_reads


def test_shared_phase_state_classification_reads_only_linear_characters() -> None:
    small_bytes, small_reads = _phase_reentry_lexer_reads(2_048)
    large_bytes, large_reads = _phase_reentry_lexer_reads(4_096)

    assert large_bytes > small_bytes
    assert large_reads <= (3 * small_reads) + large_bytes
    assert large_reads <= 12 * large_bytes


def test_resolution_wait_pre_due_sweeps_append_nothing_and_do_not_hot_loop(
    tmp_path, workflow_writer
) -> None:
    """Catch coordinator sweeps mutating or polling a not-yet-due resolution read."""
    path = workflow_writer(
        tmp_path / "resolution-bound-package",
        name="resolution-bound",
        nodes=[
            {"id": "producer", "bash": "true"},
            {"id": "consumer", "bash": "true", "depends_on": ["producer"]},
        ],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(path)
    store = RunStore(tmp_path / "resolution-bound-home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key="resolution-bound",
            concurrency_key="resolution-bound",
        ),
        immutable_snapshot=prepared,
    )
    observed = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    identity = {
        "node_id": "producer",
        "attempt_id": "attempt-winner",
        "publication_id": "a" * 32,
        "sha256": "b" * 64,
        "size_bytes": 5,
        "media_type": "text/markdown; charset=utf-8",
        "schema_fingerprint": None,
        "canonicalization_version": 1,
        "output_type": "text",
    }
    assert store.defer_output_resolution(
        admitted.run_id,
        "consumer",
        producer_identity=identity,
        now=observed,
    )
    before = store.load_run(admitted.run_id)
    started = time.perf_counter()

    for offset in range(1_000):
        assert store.wake_due_output_resolutions(
            admitted.run_id,
            now=observed + timedelta(microseconds=offset),
        ) == ()

    after = store.load_run(admitted.run_id)
    assert time.perf_counter() - started < 2.0
    assert after["event_sequence"] == before["event_sequence"]
    assert after["state_version"] == before["state_version"]
    assert after["nodes"]["consumer"] == before["nodes"]["consumer"]


def test_thousand_node_projection_is_bounded_and_disables_mermaid(
    tmp_path, workflow_writer
) -> None:
    nodes = [
        {"id": f"node-{index:04d}", "bash": "true", **({"depends_on": [f"node-{index - 1:04d}"]} if index else {})}
        for index in range(1000)
    ]
    package = load_workflow(
        workflow_writer(tmp_path / "large", name="large", nodes=nodes)
    )
    started = time.perf_counter()
    result = project_topology(package.definition)
    elapsed = time.perf_counter() - started

    assert result.node_count == 1000
    assert result.edge_count == 999
    assert len(result.text.encode("utf-8")) <= 12 * 1024
    assert result.mermaid is None
    assert "topology_mermaid_too_many_nodes" in result.warnings
    assert elapsed < 2.0


def test_ten_thousand_expired_coordinator_diagnostics_are_pruned_without_losing_wakes(
    tmp_path,
) -> None:
    store = RunStore(tmp_path / "home")
    now = datetime.now(timezone.utc)
    expired = (now - timedelta(days=8)).isoformat()
    with store._connect() as connection:
        connection.executemany(
            "INSERT INTO coordinator_events "
            "(timestamp, event_type, owner_id, epoch, payload_json) "
            "VALUES (?, 'diagnostic', 'owner', 1, '{}')",
            ((expired,) for _ in range(10_000)),
        )
        connection.executemany(
            "INSERT INTO coordinator_wakes "
            "(run_id, reason_code, created_at, completed_at, completed_epoch, outcome) "
            "VALUES (?, 'test', ?, ?, 1, 'processed')",
            ((f"completed-{index}", expired, expired) for index in range(9_999)),
        )
        connection.execute(
            "INSERT INTO coordinator_wakes (run_id, reason_code, created_at) "
            "VALUES ('unprocessed', 'test', ?)",
            (expired,),
        )
        record_coordinator_wake(
            connection, run_id="fresh", reason_code="test", now=now
        )
        event_count = connection.execute(
            "SELECT COUNT(*) FROM coordinator_events"
        ).fetchone()[0]
        wakes = connection.execute(
            "SELECT run_id, completed_at FROM coordinator_wakes ORDER BY generation"
        ).fetchall()

    assert event_count == 0
    assert [(row["run_id"], row["completed_at"]) for row in wakes] == [
        ("unprocessed", None),
        ("fresh", None),
    ]


def test_topology_injection_canaries_remain_strict_graph_grammar(
    tmp_path, workflow_writer
) -> None:
    node_id = "x%%{init:evil}%%-script-alert-1-click-style-class-quote-newline"
    package = load_workflow(
        workflow_writer(tmp_path / "canary", nodes=[{"id": node_id, "bash": "true"}])
    )
    result = project_topology(package.definition)

    assert result.mermaid is not None
    assert "%%" not in result.mermaid
    assert "<" not in result.mermaid
    assert "click " not in result.mermaid
    assert result.mermaid.splitlines()[0] == "flowchart LR"


def test_coordinator_cursor_reaches_run_201_with_bounded_keyset_pages(
    tmp_path, workflow_writer
) -> None:
    home = tmp_path / "home"
    store = RunStore(
        home,
        max_executing_runs=300,
        max_nonterminal_runs=300,
        max_total_workers=300,
        max_start_requests_per_minute=300,
    )
    now = datetime.now(timezone.utc)
    coordinator = CoordinatorStore(store.database)
    identity = CoordinatorIdentity("cursor-owner", "gateway", "cursor-host", 1, None)
    leadership = coordinator.try_acquire(identity, now=now, lease_seconds=600)
    assert leadership.is_leader
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="cursor-work")
    )
    admitted = []
    for index in range(205):
        prepared = store.prepare_run_snapshot(package)
        admitted.append(
            store.start_run(
                RunAdmissionRequest(
                    workflow_name=package.definition.name,
                    definition_digest=prepared.definition_digest,
                    policy_digest=prepared.policy_digest,
                    input_manifest_digest=prepared.input_manifest_digest,
                    trigger_source="api",
                    idempotency_key=f"cursor-{index:03d}",
                    concurrency_key=f"cursor-{index:03d}",
                    concurrency_policy="allow",
                    execution_mode="background",
                ),
                immutable_snapshot=prepared,
            ).run_id
        )
    with store._connect() as connection:
        query_plan = tuple(
            str(row["detail"])
            for row in connection.execute(
                "EXPLAIN QUERY PLAN SELECT run_id, created_at, status, "
                "execution_mode FROM runs WHERE admission_state='published' "
                "AND status IN ('queued','running','waiting_retry') "
                "AND execution_mode IN ('background','foreground') "
                "ORDER BY created_at, run_id LIMIT 101"
            )
        )
    assert any("runs_coordinator_scan" in detail for detail in query_plan)
    assert not any("TEMP B-TREE" in detail for detail in query_plan)

    scheduler = MagicMock()
    scheduler.submit.return_value = True
    service = WorkflowCoordinatorService(
        BackgroundServiceContext(
            host_kind="gateway",
            host_instance_id="cursor-host",
        ),
        hermes_home=home,
    )
    cursor = None
    for _page in range(10):
        started = time.monotonic()
        _actionable, cursor, _progress = service._sweep_once(
            store,
            coordinator,
            identity,
            leadership.lease.epoch,
            scheduler,
            cursor,
        )
        assert time.monotonic() - started < 2.2
        if cursor is None and scheduler.submit.call_count >= len(admitted):
            break

    submitted = [call.args[0] for call in scheduler.submit.call_args_list]
    assert set(submitted) == set(admitted)
    assert admitted[200] in submitted
    assert len(submitted) == 205


def test_stall_threshold_transitions_use_exact_monotonic_boundaries_and_deduplicate(
    tmp_path, workflow_writer
) -> None:
    base = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    sample = LeaseClockSample(base, 100.0, "boot-a")
    current_sample = [sample]
    store = RunStore(tmp_path / "home", lease_clock=lambda: current_sample[0])
    identity = CoordinatorIdentity(
        "threshold-owner", "gateway", "threshold-host", 1, None
    )
    coordinator = CoordinatorStore(
        store.database, clock=lambda: current_sample[0]
    )
    leadership = coordinator.try_acquire(identity, now=base, lease_seconds=600)
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="stall-threshold")
    )
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="stall-threshold",
            concurrency_key=package.definition.name,
            execution_mode="background",
        ),
        immutable_snapshot=prepared,
    )
    projection = store.load_run(admitted.run_id)
    nodes = {key: dict(value) for key, value in projection["nodes"].items()}
    nodes["start"]["state"] = "succeeded"
    store.append_event(
        admitted.run_id,
        "fault_injected_pending_finalization",
        projection_updates={
            "nodes": nodes,
            "last_runnable_progress_at": base.isoformat(),
            "last_runnable_progress_monotonic": 100.0,
            "progress_boot_id": "boot-a",
        },
    )
    fence = ExecutionFence("threshold-owner", leadership.lease.epoch)

    assert not store.record_stall_if_due(
        admitted.run_id,
        fence=fence,
        now=LeaseClockSample(base + timedelta(seconds=59), 159.999, "boot-a"),
        runnable_stall_seconds=60,
        semantic_stall_seconds=300,
    )
    assert store.record_stall_if_due(
        admitted.run_id,
        fence=fence,
        now=LeaseClockSample(base + timedelta(seconds=60), 160.0, "boot-a"),
        runnable_stall_seconds=60,
        semantic_stall_seconds=300,
    )
    assert not store.record_stall_if_due(
        admitted.run_id,
        fence=fence,
        now=LeaseClockSample(base + timedelta(seconds=61), 161.0, "boot-a"),
        runnable_stall_seconds=60,
        semantic_stall_seconds=300,
    )
    events = [
        event
        for event in store.tail_events(admitted.run_id)
        if event["event_type"] == "run_stalled"
    ]
    assert len(events) == 1
    assert events[0]["payload"]["reason_code"] == "runnable_progress_stalled"

    semantic_prepared = store.prepare_run_snapshot(package)
    semantic = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=semantic_prepared.definition_digest,
            policy_digest=semantic_prepared.policy_digest,
            input_manifest_digest=semantic_prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="semantic-stall-threshold",
            concurrency_key="semantic-stall-threshold",
            execution_mode="background",
        ),
        immutable_snapshot=semantic_prepared,
    )
    claim = store.claim_node(
        semantic.run_id,
        "start",
        "threshold-worker",
        now=base,
        monotonic_now=100.0,
        execution_fence=fence,
    )
    assert claim is not None
    store.mark_node_started(claim, now=sample)
    store.append_event(semantic.run_id, "semantic_progress")
    current_sample[0] = LeaseClockSample(
        base + timedelta(seconds=1), 0.0, "boot-b"
    )
    store.append_event(semantic.run_id, "runnable_progress_after_restart")
    takeover = coordinator.try_acquire(
        identity,
        now=current_sample[0].utc_now,
        lease_seconds=600,
    )
    assert takeover.is_leader
    semantic_fence = ExecutionFence(identity.owner_id, takeover.lease.epoch)

    assert not store.record_stall_if_due(
        semantic.run_id,
        fence=semantic_fence,
        now=LeaseClockSample(base + timedelta(seconds=299), 49.999, "boot-b"),
        runnable_stall_seconds=60,
        semantic_stall_seconds=300,
    )
    assert store.record_stall_if_due(
        semantic.run_id,
        fence=semantic_fence,
        now=LeaseClockSample(base + timedelta(seconds=300), 50.0, "boot-b"),
        runnable_stall_seconds=60,
        semantic_stall_seconds=300,
    )
    semantic_events = [
        event
        for event in store.tail_events(semantic.run_id)
        if event["event_type"] == "run_stalled"
    ]
    assert len(semantic_events) == 1
    assert semantic_events[0]["payload"]["reason_code"] == (
        "semantic_progress_stalled"
    )
