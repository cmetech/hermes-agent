from __future__ import annotations

import pytest

from plugins.workflow.language_schema import (
    DurableWorkflowCode,
    validate_durable_code_emitters,
)
from plugins.workflow.models import WorkflowLanguageProfile


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


def test_phase4_durable_code_without_an_emitter_is_rejected() -> None:
    """Catch catalog-only v4 failures that have no executable behavior test."""
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

    with pytest.raises(RuntimeError, match="include_cycle"):
        validate_durable_code_emitters((include_cycle,), emitted_codes=set())
