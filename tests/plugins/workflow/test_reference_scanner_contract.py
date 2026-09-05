from __future__ import annotations

from collections.abc import Mapping
import json
import subprocess
import sys

import pytest

import plugins.workflow.bash_rendering as bash_rendering
import plugins.workflow.language_schema as language_schema
import plugins.workflow.resources as resources
from plugins.workflow.language_schema import (
    ARCHON_V3_NODE_ID_PATTERN,
    ARCHON_V3_OUTPUT_PATH_SEGMENT_PATTERN,
    WorkflowLanguageProfile as P,
    iter_interpolation_surface_templates,
    workflow_authoring_contract,
)
from plugins.workflow.models import WorkflowValidationError
from plugins.workflow.schema import (
    _compile_workflow_source_document,
    parse_workflow_source_bytes,
)


REQUIRED_SECTIONS = {
    "version",
    "applicability",
    "grammar",
    "boundaries",
    "offsets",
    "unicode_profiles",
    "modes",
    "scalars",
    "callers",
    "diagnostics",
    "interpolation_surface",
}


def _path_text(path: tuple[str, ...]) -> str:
    rendered = ""
    for part in path:
        rendered += part if not rendered or part == "[]" else f".{part}"
    return rendered


def _padded_value(value: Mapping[str, object], target_bytes: int) -> dict[str, object]:
    baseline = len(language_schema.canonical_contract_json(value).encode())
    with_padding = {**value, "x-task2-padding": ""}
    overhead = (
        len(language_schema.canonical_contract_json(with_padding).encode())
        - baseline
    )
    assert target_bytes >= baseline + overhead
    return {
        **value,
        "x-task2-padding": "x" * (target_bytes - baseline - overhead),
    }


def _scanner() -> dict[str, object]:
    contract = workflow_authoring_contract(P.ARCHON_2026_07, normalizer_version=6)
    return contract["reference_scanner_v1"]


def _bash_candidate_spans(template: str) -> tuple[tuple[int, int], ...]:
    outputs = tuple(language_schema.iter_output_reference_candidate_spans(
        template,
        normalizer_version=6,
    ))
    scalars = tuple(
        match.span()
        for match in bash_rendering._BASH_SCALAR_REFERENCE.finditer(template)
        if match.group("position") is not None
        or match.group("name") in bash_rendering._BASH_SCALAR_NAMES
    )
    return tuple(sorted(set((*outputs, *scalars))))


def _normalize_v6_without_admission(path):
    source = parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=b"language_compatibility: archon-2026-07\n",
        source="project",
        precedence=1,
    )
    return _compile_workflow_source_document(source, normalizer_version=6)


def test_v6_requires_complete_reference_scanner_metadata():
    contract = workflow_authoring_contract(P.ARCHON_2026_07, normalizer_version=6)

    assert contract["contract_reader_version"] == 3
    scanner = contract["reference_scanner_v1"]
    assert scanner["version"] == 1
    assert set(scanner) == REQUIRED_SECTIONS
    assert scanner["grammar"]["node_id"] == ARCHON_V3_NODE_ID_PATTERN
    assert (
        scanner["grammar"]["path_segment"]
        == ARCHON_V3_OUTPUT_PATH_SEGMENT_PATTERN
    )


@pytest.mark.parametrize(
    ("profile", "version"),
    [
        (P.HERMES_LEGACY, 1),
        (P.HERMES_LEGACY, 2),
        *[(P.ARCHON_2026_07, version) for version in range(1, 6)],
    ],
)
def test_historical_contracts_do_not_advertise_the_new_reader(profile, version):
    contract = workflow_authoring_contract(profile, normalizer_version=version)

    assert contract["contract_reader_version"] == 2
    assert "reference_scanner_v1" not in contract
    assert contract["limits"] == {
        "max_document_bytes": 2 * 1024 * 1024,
        "max_contract_bytes": 288_000,
        "reserved_growth_bytes": 4_000,
        "section_max_bytes": {
            "definition_schema": 160_000,
            "node_kinds": 72_000,
            "compatibility_codes": 19_000,
        },
    }


def test_complete_surface_is_an_inventory_derived_root_body_and_control_projection():
    surface = language_schema.reference_scanner_interpolation_surface()
    fields = surface["fields"]
    observed = [
        (
            field["scope"],
            field["relative_path"],
            field["field_path"],
            tuple(field["node_types"]),
        )
        for field in fields
    ]
    expected: list[tuple[str, str, str, tuple[str, ...]]] = []
    for record in language_schema._INTERPOLATION_SURFACE_INVENTORY:
        leaves = record.ordered_leaf_paths or ((),)
        relative_paths = tuple(
            _path_text((*record.field_path, *leaf)) for leaf in leaves
        )
        if record.field_path[0] == "loop_group":
            expected.extend(
                (
                    "group-control",
                    relative_path,
                    f"nodes[].{relative_path}",
                    ("loop_group",),
                )
                for relative_path in relative_paths
            )
            continue
        root_types = tuple(sorted(record.node_types))
        body_types = tuple(sorted(record.node_types.intersection(language_schema.NODE_TYPES)))
        expected.extend(
            (
                "root",
                relative_path,
                f"nodes[].{relative_path}",
                root_types,
            )
            for relative_path in relative_paths
        )
        expected.extend(
            (
                "body",
                relative_path,
                f"nodes[].loop_group.nodes[].{relative_path}",
                body_types,
            )
            for relative_path in relative_paths
        )

    assert observed == expected
    assert [field["scope"] for field in fields].count("root") == 18
    assert [field["scope"] for field in fields].count("body") == 18
    assert [field["scope"] for field in fields].count("group-control") == 2

    paired = {
        (field["scope"], field["relative_path"]): set(field["node_types"])
        for field in fields
    }
    for relative_path in {
        field["relative_path"] for field in fields if field["scope"] == "root"
    }:
        assert paired[("body", relative_path)] == (
            paired[("root", relative_path)] & set(language_schema.NODE_TYPES)
        )


def test_complete_surface_publishes_controls_phase4_leaves_and_field_policies():
    surface = _scanner()["interpolation_surface"]
    assert surface["field_defaults"] == {"phase4_only": False}
    fields = surface["fields"]
    by_path = {field["field_path"]: field for field in fields}

    assert {
        path for path, field in by_path.items() if field["scope"] == "group-control"
    } == {
        "nodes[].loop_group.until_bash",
        "nodes[].loop_group.gate_message",
    }
    phase4_root = {
        path for path, field in by_path.items()
        if field["scope"] == "root"
        and field.get("phase4_only", surface["field_defaults"]["phase4_only"])
    }
    assert phase4_root == {
        "nodes[].loop.gate_message",
        "nodes[].loop.command",
        "nodes[].systemPrompt",
        "nodes[].agents.*.description",
        "nodes[].agents.*.prompt",
        "nodes[].hooks.*[].response.systemMessage",
        "nodes[].hooks.*[].response.stopReason",
        "nodes[].hooks.*[].response.hookSpecificOutput.permissionDecisionReason",
        "nodes[].hooks.*[].response.hookSpecificOutput.additionalContext",
    }
    assert by_path["nodes[].command"]["authenticated_body_source"] == "command_bodies"
    assert by_path["nodes[].script"]["authenticated_body_source"] == "named_script_bodies"
    assert by_path["nodes[].script"]["value_discriminator"] == "script-inline-v1"
    assert by_path["nodes[].when"]["scanner_mode"] == "condition-v3"
    assert by_path["nodes[].loop_group.nodes[].when"]["scanner_mode"] == "condition-v6"
    assert by_path["nodes[].bash"]["caller_policy"] == "root-bash-references"
    assert (
        by_path["nodes[].loop_group.until_bash"]["caller_policy"]
        == "group-until-bash-references"
    )


def test_grouped_surface_describes_container_major_runtime_order():
    surface = language_schema.reference_scanner_interpolation_surface()
    assert surface["traversal"] == {
        "record_order": "inventory",
        "mapping_container_order": "authored",
        "sequence_container_order": "authored",
        "leaf_order": "ordered_leaf_paths",
        "nested_container_order": "container-major",
    }
    command_groups = []
    for group in surface["groups"]:
        root = next(
            (
                scope
                for scope in group["scopes"]
                if scope["scope"] == "root" and "command" in scope["node_types"]
            ),
            None,
        )
        if root is not None:
            command_groups.append(
                (group["relative_path"], tuple(group["ordered_leaf_paths"]))
            )
    assert command_groups == [
        ("when", ()),
        ("command", ()),
        ("systemPrompt", ()),
        ("agents.*", ("description", "prompt")),
        (
            "hooks.*[].response",
            (
                "systemMessage",
                "stopReason",
                "hookSpecificOutput.permissionDecisionReason",
                "hookSpecificOutput.additionalContext",
            ),
        ),
    ]

    options = {
        "when": "when",
        "systemPrompt": "system",
        "agents": {
            "alpha": {"description": "alpha-d", "prompt": "alpha-p"},
            "beta": {"description": "beta-d", "prompt": "beta-p"},
        },
        "hooks": {
            "PreToolUse": [{"response": {
                "systemMessage": "pre-system",
                "stopReason": "pre-stop",
                "hookSpecificOutput": {
                    "permissionDecisionReason": "pre-reason",
                    "additionalContext": "pre-context",
                },
            }}],
            "PostToolUse": [{"response": {
                "systemMessage": "post-system",
                "stopReason": "post-stop",
                "hookSpecificOutput": {
                    "permissionDecisionReason": "post-reason",
                    "additionalContext": "post-context",
                },
            }}],
        },
    }
    observed = list(iter_interpolation_surface_templates(
        "command",
        "command-name",
        options,
        node_id="subject",
        command_bodies={"subject": "command-body"},
        include_phase4_templates=True,
    ))
    assert [path for path, _value in observed] == [
        "when",
        "command",
        "systemPrompt",
        "agents.alpha.description",
        "agents.alpha.prompt",
        "agents.beta.description",
        "agents.beta.prompt",
        "hooks.PreToolUse[0].response.systemMessage",
        "hooks.PreToolUse[0].response.stopReason",
        "hooks.PreToolUse[0].response.hookSpecificOutput.permissionDecisionReason",
        "hooks.PreToolUse[0].response.hookSpecificOutput.additionalContext",
        "hooks.PostToolUse[0].response.systemMessage",
        "hooks.PostToolUse[0].response.stopReason",
        "hooks.PostToolUse[0].response.hookSpecificOutput.permissionDecisionReason",
        "hooks.PostToolUse[0].response.hookSpecificOutput.additionalContext",
    ]


def test_grammar_boundaries_and_scalars_match_the_existing_apis():
    scanner = _scanner()
    grammar = scanner["grammar"]
    boundaries = scanner["boundaries"]
    scalars = scanner["scalars"]

    assert boundaries["candidate_end"]["text"]["characters"] == (
        " \t\r\n'\"(){}<>=!&|,;:"
    )
    assert boundaries["candidate_end"]["bash"]["characters"] == (
        "$ \t\r\n'\"(){}<>=!&|,;:"
    )
    assert boundaries["complete_reference_suffix"] == {
        "reject_ascii_characters": ".[\\/-_",
        "reject_ascii_alphanumeric": True,
        "reject_every_non_ascii": True,
        "otherwise": "complete",
    }
    assert boundaries["malformed_suffixes"] == [
        ".outputx",
        ".output_",
        ".output/path",
        ".output[0]",
        ".output.01",
        ".output\\field",
        ".output-field",
        ".outputé",
    ]
    assert grammar["named_scalar"] == r"\$[A-Z][A-Z0-9_]*"
    assert grammar["positional_scalar"] == r"\$[1-9][0-9]*"
    assert scalars["match"] == "maximal"
    assert scalars["names"] == sorted(bash_rendering._BASH_SCALAR_NAMES)
    assert len(scalars["names"]) == 10
    assert scalars["pattern"] == bash_rendering._BASH_SCALAR_REFERENCE.pattern
    assert scalars["pattern"] == resources._SCALAR_VARIABLE.pattern
    assert scalars["text"]["comments_and_escapes"] == "not-suppressed"
    assert scalars["bash"]["lexical_context"] == "classified-with-output-candidates"
    assert scalars["arguments"]["split"] == ["shlex.split", "plain-whitespace-fallback"]
    assert scalars["replacement_rescan"] is False


def test_modes_publish_every_bash_state_family_and_precedence():
    bash = _scanner()["modes"]["bash"]

    assert bash["phase_order"] == [
        "candidate-discovery",
        "lexical-admission",
        "strict-reference-parsing",
    ]
    assert bash["no_candidate_validation"] == "shell-state-validation-still-runs"
    assert bash["nesting_limit"] == 64
    expected_families = {
        "quotes",
        "physical-continuations",
        "escapes",
        "dollar-doubling",
        "comments",
        "command-substitution",
        "backtick-substitution",
        "ansi-c-quoting",
        "parameter-expansion",
        "arithmetic-expansion",
        "legacy-arithmetic",
        "conditionals",
        "extglobs",
        "brace-expansion",
        "arrays-and-subscripts",
        "declaration-flags",
        "assignments",
        "command-position",
        "functions",
        "coprocesses",
        "case-arms",
        "redirections",
        "heredoc-delimiters",
        "heredoc-bodies",
        "here-strings",
        "unterminated-or-ambiguous",
        "nesting-boundary",
    }
    rules = bash["rules"]
    assert rules and all(
        set(rule) == {"id", "families", "classification", "examples"}
        and rule["examples"]
        for rule in rules
    )
    assert set(bash["state_families"]) == expected_families
    assert {
        family for rule in rules for family in rule["families"]
    } == expected_families
    assert {rule["classification"] for rule in rules} == {
        "admitted",
        "literal",
        "rejected",
    }
    assert bash["malformed_precedence"] == {
        "literal_candidate": "ignored-before-strict-grammar",
        "live_candidate": "lexical-context-before-strict-grammar",
    }


def test_every_published_bash_example_matches_the_runtime_classifier():
    for rule in _scanner()["modes"]["bash"]["rules"]:
        for example in rule["examples"]:
            spans = _bash_candidate_spans(example)
            assert spans, (rule["id"], example)
            if rule["classification"] == "rejected":
                with pytest.raises(
                    bash_rendering.BashRenderingError,
                    match="unsupported shell context|unterminated|ambiguous",
                ):
                    bash_rendering.classify_bash_reference_spans(example, spans)
                continue
            admitted = bash_rendering.classify_bash_reference_spans(example, spans)
            assert bool(admitted) is (rule["classification"] == "admitted"), (
                rule["id"],
                example,
            )


def test_candidate_marker_and_first_character_metadata_match_runtime_admission():
    discovery = _scanner()["boundaries"]["reference_like_candidate"]
    assert discovery["marker_suffix"] == {
        ".output": "any",
        "/output": "end-or-one-of-.[]/\\",
        "\\output": "end-or-one-of-.[]/\\",
    }
    assert discovery["first_character"] == {
        "iter_output_reference_candidate_spans": (
            "underscore-or-alphanumeric-or-non-ascii"
        ),
        "iter_output_references": "unfiltered",
    }

    for template, expected in (
        ("$a.outputx", ((0, 10),)),
        ("$a/output", ((0, 9),)),
        ("$a/outputx", ()),
        ("$a\\output", ((0, 9),)),
        ("$a\\outputx", ()),
    ):
        assert tuple(language_schema.iter_output_reference_candidate_spans(
            template,
            normalizer_version=3,
        )) == expected

    assert tuple(language_schema.iter_output_reference_candidate_spans(
        "$?a.output",
        normalizer_version=3,
    )) == ()
    with pytest.raises(language_schema.WorkflowReferenceSyntaxError) as raised:
        tuple(language_schema.iter_output_references(
            "$?a.output",
            normalizer_version=3,
        ))
    assert raised.value.start == 0


def test_callers_publish_exact_versions_scan_order_and_failure_translation():
    callers = _scanner()["callers"]

    assert set(callers) == {
        "iter_output_references",
        "iter_loop_previous_output_references",
        "iter_output_reference_candidate_spans",
        "iter_output_references_in_spans",
        "contains_output_reference",
        "iter_when_output_references",
        "validate_v3_condition_syntax",
        "validate_v6_condition_syntax",
        "classify_bash_reference_spans",
        "bash_output_references",
        "bash_loop_previous_output_references",
        "StrictSubstitutionRenderer.resolve_outputs",
        "StrictSubstitutionRenderer.render_outputs",
        "StrictSubstitutionRenderer.render_prompt",
        "StrictSubstitutionRenderer.render_bash",
        "validate_authenticated_resource_references",
        "compute_package_digest",
        "include_reference_rewriting",
        "scheduler_authenticated_preflight",
    }
    assert callers["iter_output_references"]["normalizer_versions"] == [3, 4, 5, 6]
    assert callers["iter_output_references"]["consumption"] == "lazy"
    previous_caller = callers["iter_loop_previous_output_references"]
    assert previous_caller["normalizer_versions"] == [3, 4, 5, 6]
    assert previous_caller["workflow_activation_versions"] == [6]
    for version in previous_caller["normalizer_versions"]:
        assert tuple(language_schema.iter_loop_previous_output_references(
            "$LOOP_PREV.a.output",
            normalizer_version=version,
        )) == (language_schema.OutputReferenceToken("a", (), 0, 19),)
    assert _scanner()["applicability"]["previous_output_versions"] == [6]
    assert callers["contains_output_reference"]["malformed_candidates"] == "skip"
    assert callers["iter_when_output_references"]["reference_operands"] == "lhs-only"
    assert callers["validate_v3_condition_syntax"]["reference_operands"] == "lhs-and-reference-rhs"
    assert callers["validate_v6_condition_syntax"]["previous_outputs"] is True
    assert callers["bash_output_references"]["scan_order"] == [
        "previous",
        "equal-length-mask",
        "ordinary-and-scalar-candidates",
        "lexical-admission",
        "strict-ordinary-parse",
    ]
    assert callers["StrictSubstitutionRenderer.render_outputs"]["scalars"] is False
    assert callers["StrictSubstitutionRenderer.render_prompt"]["scalars"] is True
    assert callers["StrictSubstitutionRenderer.render_bash"]["secure_v3"] == {
        "false": "legacy-quote-context-rendering",
        "true": "bash-classifier-and-descriptor-rendering",
    }
    assert callers["validate_authenticated_resource_references"]["named_script_policy"] == {
        "normalizer_3": "presence-reject-valid-output-reference",
        "normalizers_4_to_6": "authenticated-body-scan",
    }
    assert callers["compute_package_digest"]["resource_decoding"] == {
        "command": "strict-utf8-then-command-parse",
        "named_script": "utf8-with-surrogateescape",
    }
    assert callers["include_reference_rewriting"]["normalizer_version"] == 4
    assert callers["scheduler_authenticated_preflight"]["normalizer_version"] == 3


def test_diagnostics_publish_native_and_portable_scope_mappings(
    tmp_path,
    workflow_writer,
):
    diagnostics = _scanner()["diagnostics"]

    assert set(diagnostics["stable_native_codes"]) >= {
        "output_reference_path_unsupported",
        "output_reference_not_declared_dependency",
        "structured_output_field_impossible",
        "loop_group_scope_invalid",
        "bash_reference_context_unsupported",
        "condition_runtime_syntax_invalid",
        "named_script_output_reference_unsupported",
        "invalid_command_resource",
    }
    assert diagnostics["portable_scope_codes"] == {
        "missing_dependency": "scoped-reference-missing-dependency",
        "unknown_previous_producer": "scoped-reference-unknown-producer",
        "producer_schema_required": "scoped-reference-producer-schema-required",
        "structured_path_impossible": "scoped-reference-structured-path-impossible",
        "unknown_companion_node": "scoped-companion-reference-unknown-node",
    }
    assert diagnostics["ordinary_missing_dependency"] == {
        "native": "output_reference_not_declared_dependency",
        "portable": None,
    }
    assert diagnostics["native_by_scope"]["body"] == {
        "missing_dependency": "loop_group_scope_invalid",
        "unknown_previous_producer": "loop_group_scope_invalid",
        "producer_schema_required": "loop_group_scope_invalid",
        "structured_path_impossible": "loop_group_scope_invalid",
    }
    assert diagnostics["native_by_scope"]["group-until"] == {
        "missing_dependency": "output_reference_not_declared_dependency",
        "unknown_previous_producer": "loop_group_scope_invalid",
        "producer_schema_required": "loop_group_scope_invalid",
        "structured_path_impossible": "loop_group_scope_invalid",
    }
    assert diagnostics["native_by_scope"]["group-gate"] == {
        "missing_dependency": "output_reference_not_declared_dependency",
        "producer_schema_required": "output_reference_path_unsupported",
        "structured_path_impossible": "structured_output_field_impossible",
        "dotted_key_exception": "output_reference_path_unsupported",
    }
    assert diagnostics["prose_stability"] == "not-a-public-identifier"

    closed_empty = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    dotted = {
        "type": "object",
        "properties": {"record.status": {"type": "string"}},
        "additionalProperties": False,
    }
    cases = (
        ("group-until", None, "status", "producer_schema_required"),
        ("group-until", closed_empty, "missing", "structured_path_impossible"),
        ("group-gate", None, "status", "producer_schema_required"),
        ("group-gate", closed_empty, "missing", "structured_path_impossible"),
        ("group-gate", dotted, "record.status", "dotted_key_exception"),
    )
    for index, (scope, schema, path_part, condition) in enumerate(cases):
        producer = {"id": "producer", "prompt": "produce"}
        if schema is not None:
            producer["output_format"] = schema
        reference = f"$producer.output.{path_part}"
        group_value = {
            "until": "<promise>DONE</promise>",
            "max_iterations": 2,
            "nodes": [producer] if scope == "group-until" else [
                {"id": "inside", "prompt": "consume"}
            ],
        }
        group = {"id": "group", "loop_group": group_value}
        nodes = [group]
        if scope == "group-until":
            group_value["until_bash"] = f"test -n '{reference}'"
        else:
            group_value["gate_message"] = reference
            group["depends_on"] = ["producer"]
            nodes.insert(0, producer)
        path = workflow_writer(
            tmp_path / str(index),
            nodes=nodes,
        )
        with pytest.raises(WorkflowValidationError) as raised:
            _normalize_v6_without_admission(path)
        assert raised.value.issues[0].code == (
            diagnostics["native_by_scope"][scope][condition]
        )


def test_unicode_profiles_are_fixed_data_and_offsets_are_authored_code_points():
    scanner = _scanner()

    assert scanner["offsets"] == {
        "unit": "unicode-code-point",
        "range": "half-open",
        "coordinate_space": "authored-input",
        "replacement_effect": "none",
        "utf16_conversion": "studio-editor-boundary-only",
    }
    unicode_profiles = scanner["unicode_profiles"]
    assert unicode_profiles["profiles"] == [
        {"id": "python-3.11-unicode-14.0.0", "python": "3.11", "unicode_database": "14.0.0"},
        {"id": "python-3.12-unicode-15.0.0", "python": "3.12", "unicode_database": "15.0.0"},
        {"id": "python-3.13-unicode-15.1.0", "python": "3.13", "unicode_database": "15.1.0"},
    ]
    assert set(unicode_profiles["predicates"]) == {
        "alphabetic",
        "digit",
        "alphanumeric",
        "whitespace",
    }
    assert unicode_profiles["selection"] == "explicit-profile-id"
    assert unicode_profiles["unknown_profile"] == "unsupported"
    assert unicode_profiles["published_inputs"] == "unicode-scalar-values-only"
    assert unicode_profiles["direct_python_api_lone_surrogates"] == "runtime-characterization-only"


def test_builder_returns_fresh_json_compatible_metadata():
    first = _scanner()
    second = _scanner()

    first["grammar"]["node_id"] = "changed"
    first["interpolation_surface"]["fields"][0]["field_path"] = "changed"

    assert second["grammar"]["node_id"] == ARCHON_V3_NODE_ID_PATTERN
    assert second["interpolation_surface"]["fields"][0]["field_path"] != "changed"
    json.dumps(second)


def test_v6_contract_bounds_use_trusted_profile_version_limits():
    contract = workflow_authoring_contract(P.ARCHON_2026_07, normalizer_version=6)
    assert contract["limits"] == {
        "max_document_bytes": 2 * 1024 * 1024,
        "max_contract_bytes": 328_000,
        "reserved_growth_bytes": 4_000,
        "section_max_bytes": {
            "definition_schema": 160_000,
            "node_kinds": 72_000,
            "compatibility_codes": 19_000,
            "reference_scanner_v1": 32_000,
        },
    }

    exact_total = _padded_value(contract, 328_000 - 4_000)
    language_schema._require_contract_bounds(exact_total)
    with pytest.raises(ValueError, match="contract exceeds 324000 bytes"):
        language_schema._require_contract_bounds({
            **exact_total,
            "x-task2-padding": f"{exact_total['x-task2-padding']}x",
        })

    exact_scanner = _padded_value(contract["reference_scanner_v1"], 32_000)
    section_boundary = {**contract, "reference_scanner_v1": exact_scanner}
    language_schema._require_contract_bounds(section_boundary)
    with pytest.raises(ValueError, match="reference_scanner_v1 exceeds 32000 bytes"):
        language_schema._require_contract_bounds({
            **section_boundary,
            "reference_scanner_v1": {
                **exact_scanner,
                "x-task2-padding": f"{exact_scanner['x-task2-padding']}x",
            },
        })

    forged = _padded_value(
        {
            **contract,
            "limits": {
                **contract["limits"],
                "max_contract_bytes": 99_000_000,
                "section_max_bytes": {
                    **contract["limits"]["section_max_bytes"],
                    "reference_scanner_v1": 99_000_000,
                },
            },
        },
        324_001,
    )
    with pytest.raises(ValueError, match="contract exceeds 324000 bytes"):
        language_schema._require_contract_bounds(forged)


def test_v6_root_strict_reference_paths_come_from_the_complete_root_projection():
    contract = workflow_authoring_contract(P.ARCHON_2026_07, normalizer_version=6)
    strict = next(
        rule for rule in contract["semantic_rules"]
        if rule["id"] == "strict-output-reference"
    )
    root_paths = [
        field["field_path"]
        for field in contract["reference_scanner_v1"]["interpolation_surface"]["fields"]
        if field["scope"] == "root"
    ]

    assert strict["field_paths"] == root_paths
    assert len(root_paths) == 18


def test_neutral_builder_and_contract_generation_do_not_initialize_scanners():
    builder_probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, sys; "
                "import plugins.workflow.reference_scanner_contract; "
                "print(json.dumps(sorted(name for name in sys.modules "
                "if name.startswith('plugins.workflow.'))))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(builder_probe.stdout) == [
        "plugins.workflow.reference_scanner_contract"
    ]

    contract_probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, sys; "
                "from plugins.workflow.language_schema import workflow_authoring_contract; "
                "from plugins.workflow.models import WorkflowLanguageProfile as P; "
                "workflow_authoring_contract(P.ARCHON_2026_07, normalizer_version=6); "
                "forbidden={'plugins.workflow.bash_rendering','plugins.workflow.resources',"
                "'plugins.workflow.schema','plugins.workflow.conditions'}; "
                "print(json.dumps(sorted(forbidden.intersection(sys.modules))))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(contract_probe.stdout) == []
