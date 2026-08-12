from __future__ import annotations

from dataclasses import replace
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
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.includes import expand_workflow_source
from plugins.workflow.models import (
    ExecutionFence,
    WorkflowCompilationLimits,
    WorkflowValidationError,
)
from plugins.workflow.schema import parse_workflow_source_bytes
from tests.plugins.workflow_history import load_recorded_v4_workflow as load_workflow
from plugins.workflow.store import RunStore
from plugins.workflow.topology import project_topology


def _include_source(path, *, sidecar_bytes=None):
    return parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=sidecar_bytes,
        source="project",
        precedence=1,
    )


def _limits(**changes):
    values = {
        "max_include_depth": 3,
        "max_dependencies": 64,
        "max_nodes": 512,
        "max_edges": 4096,
        "max_source_bytes": 2 * 1024 * 1024,
        "max_expanded_bytes": 2 * 1024 * 1024,
    }
    values.update(changes)
    return WorkflowCompilationLimits(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("max_include_depth", 4),
        ("max_dependencies", 65),
        ("max_nodes", 513),
        ("max_edges", 4097),
        ("max_source_bytes", (2 * 1024 * 1024) + 1),
        ("max_expanded_bytes", (2 * 1024 * 1024) + 1),
    ),
)
def test_custom_compilation_limits_cannot_raise_hard_ceiling(field, value) -> None:
    """Catch caller-supplied limits weakening mandatory compilation ceilings."""
    with pytest.raises(ValueError, match="hard compilation ceiling"):
        _limits(**{field: value})


def _dependency_boundary_sources(tmp_path, workflow_writer):
    child_sources = []
    for index in range(65):
        path = workflow_writer(
            tmp_path / f"dependency-{index:02d}",
            name=f"dependency-{index:02d}",
            nodes=[{"id": "done", "bash": "true"}],
        )
        child_sources.append(_include_source(path))
    root_64_path = workflow_writer(
        tmp_path / "root-64",
        name="root-64",
        nodes=[
            {"id": f"use-{index:02d}", "include": f"dependency-{index:02d}"}
            for index in range(64)
        ],
    )
    root_65_path = workflow_writer(
        tmp_path / "root-65",
        name="root-65",
        nodes=[
            {"id": f"use-{index:02d}", "include": f"dependency-{index:02d}"}
            for index in range(65)
        ],
    )
    return _include_source(root_64_path), _include_source(root_65_path), child_sources


def test_distinct_dependency_hard_boundary_accepts_64_and_rejects_65(
    tmp_path, workflow_writer
) -> None:
    root_64, root_65, children = _dependency_boundary_sources(
        tmp_path, workflow_writer
    )
    catalog_64 = WorkflowCatalogSnapshot.capture((root_64, *children))
    catalog_65 = WorkflowCatalogSnapshot.capture((root_65, *children))

    accepted = expand_workflow_source(root_64, catalog_64)
    assert len(accepted.dependencies) == 64
    with pytest.raises(WorkflowValidationError) as exc:
        expand_workflow_source(root_65, catalog_65)
    assert exc.value.issues[0].code == "include_dependency_limit"


def _edge_boundary_source(tmp_path, workflow_writer, *, extra_edge: bool):
    producer_ids = [f"producer-{index:02d}" for index in range(64)]
    nodes = [{"id": node_id, "bash": "true"} for node_id in producer_ids]
    nodes.extend(
        {
            "id": f"consumer-{index:02d}",
            "bash": "true",
            "depends_on": producer_ids,
        }
        for index in range(64)
    )
    if extra_edge:
        nodes.append(
            {"id": "overflow", "bash": "true", "depends_on": [producer_ids[0]]}
        )
    path = workflow_writer(
        tmp_path / ("edges-4097" if extra_edge else "edges-4096"),
        name="edges",
        nodes=nodes,
    )
    return _include_source(path)


def test_expanded_edge_hard_boundary_accepts_4096_and_rejects_4097(
    tmp_path, workflow_writer
) -> None:
    accepted_source = _edge_boundary_source(
        tmp_path, workflow_writer, extra_edge=False
    )
    rejected_source = _edge_boundary_source(
        tmp_path, workflow_writer, extra_edge=True
    )

    accepted = expand_workflow_source(
        accepted_source,
        WorkflowCatalogSnapshot.capture((accepted_source,)),
    )
    assert sum(len(node.depends_on) for node in accepted.nodes) == 4096
    with pytest.raises(WorkflowValidationError) as exc:
        expand_workflow_source(
            rejected_source,
            WorkflowCatalogSnapshot.capture((rejected_source,)),
        )
    assert exc.value.issues[0].code == "include_expansion_limit"


def _workflow_bytes_of_size(name: str, size: int) -> bytes:
    prefix = f"name: {name}\ndescription: ".encode()
    suffix = b"\nnodes:\n  - id: done\n    bash: 'true'\n"
    padding = size - len(prefix) - len(suffix)
    assert padding > 0
    return prefix + (b"x" * padding) + suffix


def _aggregate_source_case(tmp_path, workflow_writer, total_bytes: int, label: str):
    root_path = workflow_writer(
        tmp_path / f"source-{label}" / "root",
        name="root",
        nodes=[{"id": "child", "include": "child"}],
    )
    child_path = tmp_path / f"source-{label}" / "child.yaml"
    child_bytes = _workflow_bytes_of_size(
        "child", total_bytes - len(root_path.read_bytes())
    )
    child_path.write_bytes(child_bytes)
    root = _include_source(root_path)
    child = _include_source(child_path)
    return root, child


def test_selected_source_byte_hard_boundary_minus_at_and_plus_one(
    tmp_path, workflow_writer
) -> None:
    hard_limit = 2 * 1024 * 1024
    below = _aggregate_source_case(tmp_path, workflow_writer, hard_limit - 1, "below")
    exact = _aggregate_source_case(tmp_path, workflow_writer, hard_limit, "exact")
    above = _aggregate_source_case(tmp_path, workflow_writer, hard_limit + 1, "above")

    assert expand_workflow_source(
        below[0], WorkflowCatalogSnapshot.capture(below)
    ).source_bytes == hard_limit - 1
    assert expand_workflow_source(
        exact[0], WorkflowCatalogSnapshot.capture(exact)
    ).source_bytes == hard_limit
    with pytest.raises(WorkflowValidationError) as exc:
        expand_workflow_source(above[0], WorkflowCatalogSnapshot.capture(above))
    assert exc.value.issues[0].code == "include_expansion_limit"


def _canonical_byte_case(tmp_path, workflow_writer, target_bytes: int, label: str):
    prefix = (
        b'{"description":"Portable workflow fixture","name":"c",'
        b'"nodes":[{"bash":"'
    )
    suffix = b'","id":"payload"}]}'
    payload_size = target_bytes - len(prefix) - len(suffix)
    assert payload_size > 0
    path = workflow_writer(
        tmp_path / f"canonical-{label}",
        name="c",
        nodes=[{"id": "payload", "bash": "x" * payload_size}],
    )
    return _include_source(path), prefix + (b"x" * payload_size) + suffix


def test_canonical_expanded_byte_hard_boundary_minus_at_and_plus_one(
    tmp_path, workflow_writer
) -> None:
    hard_limit = 2 * 1024 * 1024
    below, below_bytes = _canonical_byte_case(
        tmp_path, workflow_writer, hard_limit - 1, "below"
    )
    exact, exact_bytes = _canonical_byte_case(
        tmp_path, workflow_writer, hard_limit, "exact"
    )
    above, _above_bytes = _canonical_byte_case(
        tmp_path, workflow_writer, hard_limit + 1, "above"
    )

    assert expand_workflow_source(
        below, WorkflowCatalogSnapshot.capture((below,))
    ).canonical_definition_bytes == below_bytes
    assert expand_workflow_source(
        exact, WorkflowCatalogSnapshot.capture((exact,))
    ).canonical_definition_bytes == exact_bytes
    with pytest.raises(WorkflowValidationError) as exc:
        expand_workflow_source(above, WorkflowCatalogSnapshot.capture((above,)))
    issue = exc.value.issues[0]
    assert issue.code == "include_expansion_limit"
    assert issue.path == "nodes[0].bash"
    assert issue.source_line == above.nodes[0].field_lines["bash"]


class _TraversalBomb:
    """A source value that fails if an over-limit traversal reaches it."""


def test_dependency_limit_aborts_before_traversing_dependency_65(
    tmp_path, workflow_writer
) -> None:
    _root_64, root_65, children = _dependency_boundary_sources(
        tmp_path, workflow_writer
    )
    bomb_child = replace(
        children[64],
        nodes=(replace(children[64].nodes[0], value=_TraversalBomb()),),
    )
    catalog = WorkflowCatalogSnapshot.capture(
        (root_65, *children[:64], bomb_child)
    )

    with pytest.raises(WorkflowValidationError) as exc:
        expand_workflow_source(root_65, catalog)
    assert exc.value.issues[0].code == "include_dependency_limit"


def test_node_limit_aborts_before_materializing_node_513(
    tmp_path, workflow_writer
) -> None:
    path = workflow_writer(
        tmp_path / "nodes-513",
        name="nodes-513",
        nodes=[
            {"id": f"node-{index:03d}", "bash": "true"}
            for index in range(513)
        ],
    )
    source = _include_source(path)
    bomb_source = replace(
        source,
        nodes=(
            *source.nodes[:512],
            replace(source.nodes[512], value=_TraversalBomb()),
        ),
    )

    with pytest.raises(WorkflowValidationError) as exc:
        expand_workflow_source(
            bomb_source,
            WorkflowCatalogSnapshot.capture((bomb_source,)),
        )
    assert exc.value.issues[0].code == "include_expansion_limit"


def test_edge_limit_aborts_before_materializing_edge_4097_node(
    tmp_path, workflow_writer
) -> None:
    source = _edge_boundary_source(tmp_path, workflow_writer, extra_edge=True)
    bomb_source = replace(
        source,
        nodes=(
            *source.nodes[:-1],
            replace(source.nodes[-1], value=_TraversalBomb()),
        ),
    )

    with pytest.raises(WorkflowValidationError) as exc:
        expand_workflow_source(
            bomb_source,
            WorkflowCatalogSnapshot.capture((bomb_source,)),
        )
    assert exc.value.issues[0].code == "include_expansion_limit"


def test_source_byte_limit_aborts_before_traversing_oversized_child(
    tmp_path, workflow_writer
) -> None:
    hard_limit = 2 * 1024 * 1024
    root, child = _aggregate_source_case(
        tmp_path, workflow_writer, hard_limit + 1, "abort"
    )
    bomb_child = replace(
        child,
        nodes=(replace(child.nodes[0], value=_TraversalBomb()),),
    )

    with pytest.raises(WorkflowValidationError) as exc:
        expand_workflow_source(
            root,
            WorkflowCatalogSnapshot.capture((root, bomb_child)),
        )
    assert exc.value.issues[0].code == "include_expansion_limit"


def test_canonical_byte_limit_aborts_before_materializing_later_node(
    tmp_path, workflow_writer
) -> None:
    path = workflow_writer(
        tmp_path / "canonical-abort",
        name="canonical-abort",
        nodes=[
            {"id": "oversized", "bash": "true"},
            {"id": "unreached", "bash": "true"},
        ],
    )
    source = _include_source(path)
    bomb_source = replace(
        source,
        nodes=(
            replace(source.nodes[0], value="x" * (2 * 1024 * 1024)),
            replace(source.nodes[1], value=_TraversalBomb()),
        ),
    )

    with pytest.raises(WorkflowValidationError) as exc:
        expand_workflow_source(
            bomb_source,
            WorkflowCatalogSnapshot.capture((bomb_source,)),
        )
    issue = exc.value.issues[0]
    assert issue.code == "include_expansion_limit"
    assert issue.path == "nodes[0].bash"
    assert issue.source_line == bomb_source.nodes[0].field_lines["bash"]


def test_include_depth_and_dependency_bounds_accept_exactly_the_boundary(
    tmp_path, workflow_writer
) -> None:
    root_path = workflow_writer(
        tmp_path / "root",
        name="root",
        nodes=[
            {"id": "left", "include": "one"},
            {"id": "right", "include": "other", "depends_on": ["left"]},
        ],
    )
    one_path = workflow_writer(
        tmp_path / "one",
        name="one",
        nodes=[{"id": "next", "include": "two"}],
    )
    two_path = workflow_writer(
        tmp_path / "two",
        name="two",
        nodes=[{"id": "next", "include": "three"}],
    )
    three_path = workflow_writer(
        tmp_path / "three",
        name="three",
        nodes=[{"id": "done", "bash": "true"}],
    )
    other_path = workflow_writer(
        tmp_path / "other",
        name="other",
        nodes=[{"id": "done", "bash": "true"}],
    )
    sources = tuple(
        _include_source(path)
        for path in (root_path, one_path, two_path, three_path, other_path)
    )
    root = sources[0]
    catalog = WorkflowCatalogSnapshot.capture(sources)

    assert len(expand_workflow_source(root, catalog, _limits()).dependencies) == 4
    with pytest.raises(WorkflowValidationError) as depth:
        expand_workflow_source(root, catalog, _limits(max_include_depth=2))
    assert depth.value.issues[0].code == "include_depth_exceeded"
    with pytest.raises(WorkflowValidationError) as dependencies:
        expand_workflow_source(root, catalog, _limits(max_dependencies=3))
    assert dependencies.value.issues[0].code == "include_dependency_limit"


def test_include_node_and_edge_bounds_accept_exactly_the_boundary(
    tmp_path, workflow_writer
) -> None:
    root_path = workflow_writer(
        tmp_path / "root",
        name="root",
        nodes=[
            {"id": "build", "bash": "true"},
            {"id": "child", "include": "child", "depends_on": ["build"]},
            {"id": "publish", "bash": "true", "depends_on": ["child"]},
        ],
    )
    child_path = workflow_writer(
        tmp_path / "child",
        name="child",
        nodes=[
            {"id": "first", "bash": "true"},
            {"id": "second", "bash": "true", "depends_on": ["first"]},
        ],
    )
    root, child = _include_source(root_path), _include_source(child_path)
    catalog = WorkflowCatalogSnapshot.capture((root, child))

    expanded = expand_workflow_source(
        root,
        catalog,
        _limits(max_nodes=4, max_edges=3),
    )
    assert len(expanded.nodes) == 4
    assert sum(len(node.depends_on) for node in expanded.nodes) == 3
    with pytest.raises(WorkflowValidationError) as nodes:
        expand_workflow_source(root, catalog, _limits(max_nodes=3))
    assert nodes.value.issues[0].code == "include_expansion_limit"
    with pytest.raises(WorkflowValidationError) as edges:
        expand_workflow_source(root, catalog, _limits(max_edges=2))
    assert edges.value.issues[0].code == "include_expansion_limit"


def test_include_source_and_canonical_byte_bounds_accept_exactly_the_boundary(
    tmp_path, workflow_writer
) -> None:
    root_path = workflow_writer(
        tmp_path / "root",
        name="root",
        nodes=[{"id": "checks", "include": "child"}],
    )
    child_path = workflow_writer(
        tmp_path / "child",
        name="child",
        nodes=[{"id": "lint", "bash": "true"}],
    )
    root, child = _include_source(root_path), _include_source(child_path)
    catalog = WorkflowCatalogSnapshot.capture((root, child))
    selected_source_bytes = len(root_path.read_bytes()) + len(child_path.read_bytes())
    canonical_definition = (
        b'{"description":"Portable workflow fixture","name":"root",'
        b'"nodes":[{"bash":"true","id":"checks__lint"}]}'
    )

    expanded = expand_workflow_source(
        root,
        catalog,
        _limits(
            max_source_bytes=selected_source_bytes,
            max_expanded_bytes=len(canonical_definition),
        ),
    )
    assert expanded.source_bytes == selected_source_bytes
    assert expanded.canonical_definition_bytes == canonical_definition
    with pytest.raises(WorkflowValidationError) as sources:
        expand_workflow_source(
            root,
            catalog,
            _limits(max_source_bytes=selected_source_bytes - 1),
        )
    assert sources.value.issues[0].code == "include_expansion_limit"
    with pytest.raises(WorkflowValidationError) as canonical:
        expand_workflow_source(
            root,
            catalog,
            _limits(max_expanded_bytes=len(canonical_definition) - 1),
        )
    assert canonical.value.issues[0].code == "include_expansion_limit"


def test_v4_compilation_applies_closure_node_bound_to_a_no_include_root(
    tmp_path, workflow_writer
) -> None:
    accepted_path = workflow_writer(
        tmp_path / "accepted-root",
        name="accepted-root",
        nodes=[
            {"id": f"node-{index:03d}", "bash": "true"}
            for index in range(512)
        ],
    )
    rejected_path = workflow_writer(
        tmp_path / "rejected-root",
        name="rejected-root",
        nodes=[
            {"id": f"node-{index:03d}", "bash": "true"}
            for index in range(513)
        ],
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    accepted = _include_source(accepted_path, sidecar_bytes=sidecar)
    rejected = _include_source(rejected_path, sidecar_bytes=sidecar)

    compiled = compile_workflow(
        accepted,
        WorkflowCatalogSnapshot.capture((accepted,)),
        normalizer_version=4,
    )
    assert len(compiled.package.definition.nodes) == 512
    assert compiled.definition_bytes == accepted.definition_bytes
    with pytest.raises(WorkflowValidationError) as exc:
        compile_workflow(
            rejected,
            WorkflowCatalogSnapshot.capture((rejected,)),
            normalizer_version=4,
        )
    assert exc.value.issues[0].code == "include_expansion_limit"


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


def _continued_shell_word_lexer_reads(repetitions: int) -> tuple[int, int]:
    body = "if true; then\\\n :; fi; " * repetitions
    suffix = "printf $USER_MESSAGE"
    template = _IndexAccountingText(f'printf \'%s\' "$({body}{suffix})"')
    start = template.index("$USER_MESSAGE")

    with pytest.raises(BashRenderingError) as exc:
        classify_bash_reference_spans(
            template,
            ((start, start + len("$USER_MESSAGE")),),
        )

    assert exc.value.code == "bash_reference_context_unsupported"
    return len(template), template.index_reads


def test_continued_shell_word_authored_ends_are_classified_in_linear_time() -> None:
    small_bytes, small_reads = _continued_shell_word_lexer_reads(2_048)
    large_bytes, large_reads = _continued_shell_word_lexer_reads(4_096)

    assert large_bytes > small_bytes
    assert large_reads <= (3 * small_reads) + large_bytes
    assert large_reads <= 10 * large_bytes


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
    started = time.process_time()

    for offset in range(1_000):
        assert store.wake_due_output_resolutions(
            admitted.run_id,
            now=observed + timedelta(microseconds=offset),
        ) == ()

    after = store.load_run(admitted.run_id)
    assert time.process_time() - started < 2.0
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
    started = time.process_time()
    result = project_topology(package.definition)
    elapsed = time.process_time() - started

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
        started = time.process_time()
        _actionable, cursor, _progress = service._sweep_once(
            store,
            coordinator,
            identity,
            leadership.lease.epoch,
            scheduler,
            cursor,
        )
        assert time.process_time() - started < 2.2
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
