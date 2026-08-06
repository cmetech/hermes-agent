from __future__ import annotations

from collections.abc import Callable

import pytest

import plugins.workflow.language_schema as language_schema
from plugins.workflow.language_schema import (
    DurableWorkflowCode,
    phase4_durable_code_catalog,
)
from plugins.workflow.models import WorkflowLanguageProfile


# Each entry must call the real behavior that emits its key. Phase 4 starts
# empty; later tasks add the durable code and its emitter here together.
_PHASE4_DURABLE_CODE_EMITTERS: dict[str, Callable[[], str]] = {}


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
