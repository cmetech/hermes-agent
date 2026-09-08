from collections.abc import Mapping
from hashlib import sha256
import json
from pathlib import Path

import pytest

from plugins.workflow.language_conformance import workflow_language_conformance
from plugins.workflow.language_schema import (
    canonical_contract_json,
    workflow_authoring_contract,
)
from plugins.workflow.models import WorkflowLanguageProfile as P


BASELINES = Path(__file__).parent / "fixtures/reference_scanner/baselines"
PAIRS = [(P.HERMES_LEGACY, version, f"legacy-{version}") for version in (1, 2)] + [
    (P.ARCHON_2026_07, version, f"archon-{version}") for version in range(1, 6)
]
_MISSING = object()


def _differing_dictionary_paths(
    old: object,
    current: object,
    path: tuple[object, ...] = (),
) -> set[tuple[object, ...]]:
    if isinstance(old, Mapping) and isinstance(current, Mapping):
        paths: set[tuple[object, ...]] = set()
        for key in old.keys() | current.keys():
            paths.update(
                _differing_dictionary_paths(
                    old.get(key, _MISSING),
                    current.get(key, _MISSING),
                    (*path, key),
                )
            )
        return paths
    if (
        isinstance(old, list)
        and isinstance(current, list)
        and all(isinstance(item, Mapping) for item in [*old, *current])
    ):
        paths = set()
        for index in range(max(len(old), len(current))):
            old_item = old[index] if index < len(old) else _MISSING
            current_item = current[index] if index < len(current) else _MISSING
            paths.update(
                _differing_dictionary_paths(
                    old_item,
                    current_item,
                    (*path, index),
                )
            )
        return paths
    return set() if old == current else {path}


@pytest.mark.parametrize("profile,version,name", PAIRS)
def test_historical_contract_bytes(profile, version, name):
    expected = (BASELINES / f"{name}.json").read_bytes()
    actual = canonical_contract_json(
        workflow_authoring_contract(profile, normalizer_version=version)
    ).encode("utf-8")

    assert actual == expected


def test_legacy_corpus_bytes():
    corpus = workflow_language_conformance(P.HERMES_LEGACY)
    encoded = canonical_contract_json(corpus).encode("utf-8")

    assert (corpus["format_version"], len(corpus["cases"]), len(encoded)) == (
        1,
        11,
        7265,
    )
    assert sha256(encoded).hexdigest() == (
        "c193258148699fbcbc42c909dee10001632377272e57a0ff3b79f3493f158a3b"
    )
    assert encoded == (BASELINES / "legacy-corpus-1.json").read_bytes()


def test_baseline_manifest_matches_captured_bytes():
    manifest = json.loads((BASELINES / "manifest.json").read_text())
    artifacts = manifest["artifacts"]

    assert manifest["source_commit"] == "74fed08f91014ca7fc80ee9ea4427568ec95b04f"
    assert set(artifacts) == {
        *(f"legacy-{version}.json" for version in (1, 2)),
        *(f"archon-{version}.json" for version in range(1, 7)),
        "legacy-corpus-1.json",
    }
    assert (
        artifacts["archon-6.json"]["preservation"] == "characterization-only"
    )
    assert {
        name
        for name, metadata in artifacts.items()
        if metadata["preservation"] == "immutable"
    } == set(artifacts) - {"archon-6.json"}

    for name, metadata in artifacts.items():
        encoded = (BASELINES / name).read_bytes()
        assert len(encoded) == metadata["bytes"], name
        assert sha256(encoded).hexdigest() == metadata["sha256"], name


def test_archon_v6_changes_stay_within_the_publication_amendment():
    encoded = (BASELINES / "archon-6.json").read_bytes()
    old = json.loads(encoded)
    current = workflow_authoring_contract(P.ARCHON_2026_07, normalizer_version=6)

    assert len(encoded) == 283_522
    assert sha256(encoded).hexdigest() == (
        "171fe68bc4c8c4e5ca8a27be7212d8af556c263739f37a17235344a8e57717cc"
    )
    assert current["definition_schema"] == old["definition_schema"]
    assert current["sidecar_schema"] == old["sidecar_schema"]
    assert (
        current["editor_projection_version"]
        == old["editor_projection_version"]
        == 2
    )
    assert current["normalizer_version"] == old["normalizer_version"] == 6
    assert current["node_kinds"] == old["node_kinds"]

    strict_reference_index = next(
        index
        for index, rule in enumerate(old["semantic_rules"])
        if rule["id"] == "strict-output-reference"
    )
    allowed_changes = {
        ("contract_reader_version",),
        ("reference_scanner_v1",),
        ("contract_digest",),
        ("limits", "max_contract_bytes"),
        ("limits", "section_max_bytes", "reference_scanner_v1"),
        ("semantic_rules", strict_reference_index, "field_paths"),
    }

    assert _differing_dictionary_paths(old, current) <= allowed_changes
