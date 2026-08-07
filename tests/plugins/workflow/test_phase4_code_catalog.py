from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

import plugins.workflow.language_schema as language_schema
from plugins.workflow.compilation import WorkflowCatalogSnapshot
from plugins.workflow.dependency_manifest import _read_source_resource
from plugins.workflow.includes import DEFAULT_COMPILATION_LIMITS, expand_workflow_source
from plugins.workflow.language_schema import (
    DurableWorkflowCode,
    phase4_durable_code_catalog,
)
from plugins.workflow.models import WorkflowLanguageProfile, WorkflowValidationError
from plugins.workflow.schema import parse_workflow_source_bytes
from plugins.workflow.trust import WorkflowResourceReadBudget


def _source(
    root: Path,
    *,
    name: str,
    nodes: list[dict[str, object]],
    source_id: str,
):
    workflow_bytes = yaml.safe_dump(
        {"name": name, "description": name, "nodes": nodes},
        sort_keys=False,
    ).encode()
    return parse_workflow_source_bytes(
        root / f"{source_id}.yaml",
        workflow_bytes=workflow_bytes,
        sidecar_bytes=None,
        source="project",
        precedence=1,
    )


def _emit_include_failure(expected: str) -> str:
    """Exercise the real bounded compiler/resource path for one public code."""
    with TemporaryDirectory() as temporary:
        root_path = Path(temporary)
        root = _source(
            root_path / "root",
            name="root",
            nodes=[{"id": "use", "include": "child"}],
            source_id="root",
        )
        child = _source(
            root_path / "child",
            name="child",
            nodes=[{"id": "done", "bash": "true"}],
            source_id="child",
        )
        sources = [root, child]
        limits = DEFAULT_COMPILATION_LIMITS

        if expected == "include_not_found":
            sources = [root]
        elif expected == "include_ambiguous":
            sources.append(
                _source(
                    root_path / "duplicate",
                    name="child",
                    nodes=[{"id": "other", "bash": "true"}],
                    source_id="duplicate-child",
                )
            )
        elif expected == "include_cycle":
            child = _source(
                root_path / "child",
                name="child",
                nodes=[{"id": "back", "include": "root"}],
                source_id="child",
            )
            sources = [root, child]
        elif expected == "include_depth_exceeded":
            sources = [root]
            for name, target in (
                ("child", "two"),
                ("two", "three"),
                ("three", "four"),
            ):
                sources.append(
                    _source(
                        root_path / name,
                        name=name,
                        nodes=[{"id": f"to-{target}", "include": target}],
                        source_id=name,
                    )
                )
        elif expected == "include_dependency_limit":
            limits = replace(limits, max_dependencies=0)
        elif expected == "include_expansion_limit":
            limits = replace(limits, max_nodes=0)
        elif expected == "include_id_collision":
            root = _source(
                root_path / "root",
                name="root",
                nodes=[
                    {"id": "use__done", "bash": "true"},
                    {"id": "use", "include": "child"},
                ],
                source_id="root",
            )
            sources = [root, child]
        elif expected == "include_reference_invalid":
            child = _source(
                root_path / "child",
                name="child",
                nodes=[
                    {
                        "id": "done",
                        "prompt": "Use $outside.output",
                    }
                ],
                source_id="child",
            )
            sources = [root, child]
        elif expected == "include_empty_graph":
            sources = [root, replace(child, nodes=())]
        elif expected == "include_resource_invalid":
            budget = WorkflowResourceReadBudget(
                max_file_bytes=1_048_576,
                max_total_bytes=8_388_608,
                max_files=512,
            )
            try:
                _read_source_resource(
                    child,
                    root_path / "outside-resource.md",
                    budget,
                )
            except WorkflowValidationError as exc:
                return exc.issues[0].code
            raise AssertionError("unsafe origin resource unexpectedly resolved")
        else:  # pragma: no cover - the fixed behavior table controls callers.
            raise AssertionError(f"missing real emitter fixture for {expected}")

        try:
            expand_workflow_source(
                root,
                WorkflowCatalogSnapshot.capture(sources),
                limits,
            )
        except WorkflowValidationError as exc:
            return exc.issues[0].code
        raise AssertionError(f"real compiler did not emit {expected}")


# Every registration is tied to the real behavior that emits its stable key.
_PHASE4_DURABLE_CODE_EMITTERS: dict[str, Callable[[], str]] = {
    code: (lambda code=code: _emit_include_failure(code))
    for code in (
        "include_not_found",
        "include_ambiguous",
        "include_cycle",
        "include_depth_exceeded",
        "include_dependency_limit",
        "include_expansion_limit",
        "include_id_collision",
        "include_reference_invalid",
        "include_resource_invalid",
        "include_empty_graph",
    )
}


def _assert_phase4_durable_code_coverage() -> None:
    catalog = phase4_durable_code_catalog()
    assert set(catalog) == set(_PHASE4_DURABLE_CODE_EMITTERS)
    for code, emit in _PHASE4_DURABLE_CODE_EMITTERS.items():
        assert callable(emit)
        assert emit() == code


def test_phase4_durable_codes_keep_profile_and_minimum_version_metadata() -> None:
    """Catch a v4 code being registered without its admission boundary."""
    include_cycle = DurableWorkflowCode(
        "include_cycle",
        "an include graph cannot contain a cycle",
        "includes",
        frozenset({WorkflowLanguageProfile.ARCHON_2026_07}),
        frozenset({4}),
        False,
        True,
        False,
        ("includes",),
    )
    catalog = {include_cycle.code: include_cycle}

    assert catalog["include_cycle"].minimum_normalizer_version == 4
    assert catalog["include_cycle"].effective_profile == "archon-2026-07"


def test_registered_phase4_codes_have_real_behavior_emitters() -> None:
    """Catch a real Phase 4 registration without its behavior-linked emitter."""
    _assert_phase4_durable_code_coverage()


def test_registered_phase4_code_without_an_emitter_is_rejected(monkeypatch) -> None:
    """Catch catalog-only v4 failures before their behavior test is added."""
    include_cycle = DurableWorkflowCode(
        "include_cycle",
        "an include graph cannot contain a cycle",
        "includes",
        frozenset({WorkflowLanguageProfile.ARCHON_2026_07}),
        frozenset({4}),
        False,
        True,
        False,
        ("includes",),
    )
    monkeypatch.setattr(language_schema, "PHASE4_DURABLE_CODES", (include_cycle,))

    with pytest.raises(AssertionError, match="include_cycle"):
        _assert_phase4_durable_code_coverage()
