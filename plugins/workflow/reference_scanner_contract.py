"""Dependency-neutral declarative metadata for workflow reference scanners."""

from __future__ import annotations

from collections.abc import Mapping


_SCALAR_NAMES = (
    "ARGUMENTS",
    "USER_MESSAGE",
    "ARTIFACTS_DIR",
    "WORKFLOW_ID",
    "BASE_BRANCH",
    "DOCS_DIR",
    "CONTEXT",
    "LOOP_USER_INPUT",
    "LOOP_PREV_OUTPUT",
    "REJECTION_REASON",
)


def _bash_rules() -> list[dict[str, object]]:
    """Return reviewed examples for the classifier's observable state families."""
    return [
        {
            "id": "bash.simple-quote-contexts",
            "families": ["quotes"],
            "classification": "admitted",
            "examples": [
                "printf %s $USER_MESSAGE",
                "printf '%s' '$USER_MESSAGE'",
                'printf \'%s\' "$USER_MESSAGE"',
            ],
        },
        {
            "id": "bash.escaped-and-comment-candidates",
            "families": ["escapes"],
            "classification": "literal",
            "examples": [
                "printf %s \\$USER_MESSAGE",
            ],
        },
        {
            "id": "bash.physical-continuations",
            "families": ["physical-continuations"],
            "classification": "admitted",
            "examples": ["printf '%s' \\\n\"$USER_MESSAGE\""],
        },
        {
            "id": "bash.pid-dollar-doubling",
            "families": ["dollar-doubling"],
            "classification": "literal",
            "examples": ["printf '%s' $$USER_MESSAGE"],
        },
        {
            "id": "bash.physical-comments",
            "families": ["comments"],
            "classification": "literal",
            "examples": ["# $producer.output\nprintf safe"],
        },
        {
            "id": "bash.nested-expansions",
            "families": [
                "command-substitution",
                "backtick-substitution",
                "parameter-expansion",
                "arithmetic-expansion",
            ],
            "classification": "rejected",
            "examples": [
                "printf '%s' $(printf %s $USER_MESSAGE)",
                "printf '%s' `printf %s $USER_MESSAGE`",
                "printf '%s' ${OTHER:-$USER_MESSAGE}",
                "printf '%s' $((1 + $USER_MESSAGE))",
            ],
        },
        {
            "id": "bash.ansi-c-and-backticks",
            "families": ["ansi-c-quoting"],
            "classification": "rejected",
            "examples": ["printf %s $'$USER_MESSAGE'"],
        },
        {
            "id": "bash.arithmetic-and-conditionals",
            "families": ["legacy-arithmetic", "conditionals"],
            "classification": "rejected",
            "examples": [
                "(( $USER_MESSAGE ))",
                "echo $[$USER_MESSAGE + 1]",
                "[[ $USER_MESSAGE == value ]]",
            ],
        },
        {
            "id": "bash.word-multiplication",
            "families": ["extglobs", "brace-expansion"],
            "classification": "rejected",
            "examples": ["echo @($USER_MESSAGE|x)", "echo pre{$USER_MESSAGE,x}"],
        },
        {
            "id": "bash.assignment-contexts",
            "families": ["assignments"],
            "classification": "admitted",
            "examples": ["value=$USER_MESSAGE printf safe"],
        },
        {
            "id": "bash.array-and-integer-contexts",
            "families": ["arrays-and-subscripts", "declaration-flags"],
            "classification": "rejected",
            "examples": [
                "items[$USER_MESSAGE]=value",
                "declare -i value=$USER_MESSAGE",
                "let value=$USER_MESSAGE",
            ],
        },
        {
            "id": "bash.command-grammar",
            "families": ["command-position", "functions", "coprocesses", "case-arms"],
            "classification": "rejected",
            "examples": [
                "function $USER_MESSAGE { :; }",
                "coproc $USER_MESSAGE { :; }",
                "printf '%s' \"$(case x in x) printf $USER_MESSAGE;; esac)\"",
            ],
        },
        {
            "id": "bash.case-arm",
            "families": ["case-arms"],
            "classification": "admitted",
            "examples": ["case value in $USER_MESSAGE) :;; esac"],
        },
        {
            "id": "bash.ordinary-redirections-and-here-strings",
            "families": ["redirections", "here-strings"],
            "classification": "admitted",
            "examples": [
                "printf '%s' $USER_MESSAGE 2>/dev/null",
                "cat <<<$USER_MESSAGE",
            ],
        },
        {
            "id": "bash.heredoc-delimiters-and-bodies",
            "families": ["heredoc-delimiters", "heredoc-bodies"],
            "classification": "rejected",
            "examples": [
                "cat <<$USER_MESSAGE\nbody\n",
                "cat <<'EOF'\n$USER_MESSAGE\nEOF\n",
            ],
        },
        {
            "id": "bash.incomplete-or-over-nested-state",
            "families": ["unterminated-or-ambiguous", "nesting-boundary"],
            "classification": "rejected",
            "examples": ['printf "%s" "$USER_MESSAGE', "$(printf $USER_MESSAGE"],
        },
    ]


def _callers() -> dict[str, dict[str, object]]:
    inherited = [3, 4, 5, 6]
    return {
        "iter_output_references": {
            "normalizer_versions": inherited.copy(),
            "mode": "text",
            "consumption": "lazy",
            "scan": "left-to-right-dollar-candidates",
            "failure": "WorkflowReferenceSyntaxError-on-consumed-malformed-candidate",
        },
        "iter_loop_previous_output_references": {
            "normalizer_versions": inherited.copy(),
            "workflow_activation_versions": [6],
            "mode": "text-previous",
            "consumption": "lazy",
            "prefix": "$LOOP_PREV.",
            "failure": "WorkflowReferenceSyntaxError-on-consumed-malformed-candidate",
        },
        "iter_output_reference_candidate_spans": {
            "normalizer_versions": inherited.copy(),
            "mode": "bash-candidate-discovery",
            "consumption": "lazy",
            "nested_dollars": "searched-independently",
            "failure": "invalid-normalizer-only",
        },
        "iter_output_references_in_spans": {
            "normalizer_versions": inherited.copy(),
            "mode": "admitted-span-strict-parse",
            "consumption": "lazy",
            "span_order": "ordered-nonoverlapping-half-open",
            "failure": "ValueError-or-WorkflowReferenceSyntaxError-with-authored-start",
        },
        "contains_output_reference": {
            "normalizer_versions": inherited.copy(),
            "mode": "complete-reference-presence",
            "consumption": "eager-until-first-valid",
            "malformed_candidates": "skip",
            "search": "valid-reference-before-or-after-malformed",
        },
        "iter_when_output_references": {
            "normalizer_versions": inherited.copy(),
            "mode": "historical-condition-iterator",
            "consumption": "lazy",
            "reference_operands": "lhs-only",
            "rhs": "quoted-string-or-decimal-literal",
        },
        "validate_v3_condition_syntax": {
            "normalizer_versions": inherited.copy(),
            "mode": "condition-v3",
            "consumption": "eager",
            "reference_operands": "lhs-and-reference-rhs",
            "previous_outputs": False,
            "failure": "WorkflowConditionError-with-reference-cause-when-applicable",
        },
        "validate_v6_condition_syntax": {
            "normalizer_versions": [6],
            "mode": "condition-v6",
            "consumption": "eager",
            "reference_operands": "lhs-and-reference-rhs",
            "previous_outputs": True,
            "failure": "WorkflowConditionError-with-reference-cause-when-applicable",
        },
        "classify_bash_reference_spans": {
            "normalizer_versions": inherited.copy(),
            "mode": "bash-lexical-admission",
            "consumption": "eager",
            "input": "ordered-nonoverlapping-candidate-spans",
            "no_candidates": "shell-state-validation-still-runs",
            "failure": "ValueError-or-BashRenderingError",
        },
        "bash_output_references": {
            "normalizer_versions": inherited.copy(),
            "mode": "bash",
            "consumption": "eager",
            "scan_order": [
                "previous",
                "equal-length-mask",
                "ordinary-and-scalar-candidates",
                "lexical-admission",
                "strict-ordinary-parse",
            ],
            "previous_outputs": "enabled-at-normalizer-6",
        },
        "bash_loop_previous_output_references": {
            "normalizer_versions": [6],
            "mode": "bash-previous",
            "consumption": "eager",
            "scan_order": ["candidate-discovery", "lexical-admission", "strict-previous-parse"],
            "offsets": "restored-to-authored-input",
        },
        "StrictSubstitutionRenderer.resolve_outputs": {
            "normalizer_versions": inherited.copy(),
            "mode": "text",
            "consumption": "eager-all-templates",
            "scalars": False,
            "outputs": "deduplicated-by-previous-producer-path",
            "failure_translation": "reference-syntax-to-output-reference-with-cause",
        },
        "StrictSubstitutionRenderer.render_outputs": {
            "normalizer_versions": inherited.copy(),
            "mode": "text",
            "consumption": "eager",
            "scalars": False,
            "replacement_rescan": False,
        },
        "StrictSubstitutionRenderer.render_prompt": {
            "normalizer_versions": inherited.copy(),
            "mode": "text",
            "consumption": "eager",
            "scalars": True,
            "replacement_rescan": False,
        },
        "StrictSubstitutionRenderer.render_bash": {
            "normalizer_versions": inherited.copy(),
            "mode": "bash-when-secure-v3",
            "consumption": "eager",
            "scalars": True,
            "secure_v3": {
                "false": "legacy-quote-context-rendering",
                "true": "bash-classifier-and-descriptor-rendering",
            },
            "failure_translation": "reference-syntax-to-output-reference-with-cause",
        },
        "validate_authenticated_resource_references": {
            "normalizer_versions": inherited.copy(),
            "mode": "surface-policy",
            "command_policy": "authenticated-body-scan",
            "named_script_policy": {
                "normalizer_3": "presence-reject-valid-output-reference",
                "normalizers_4_to_6": "authenticated-body-scan",
            },
            "failure_order": "definition-then-companion-resource-traversal",
        },
        "compute_package_digest": {
            "normalizer_versions": inherited.copy(),
            "mode": "authenticated-resource-load-then-validation",
            "resource_decoding": {
                "command": "strict-utf8-then-command-parse",
                "named_script": "utf8-with-surrogateescape",
            },
            "command_failure": "invalid_command_resource-with-decoding-or-parse-cause",
            "named_script_validation": (
                "version-specific-validate_authenticated_resource_references"
            ),
        },
        "include_reference_rewriting": {
            "normalizer_versions": [4],
            "normalizer_version": 4,
            "mode": "field-specific-text-bash-or-historical-condition",
            "replacement_rescan": False,
        },
        "scheduler_authenticated_preflight": {
            "normalizer_versions": [3],
            "normalizer_version": 3,
            "mode": "bash-for-bash-node-otherwise-text",
            "scan_order": "template-order-then-reference-order",
        },
    }


def reference_scanner_contract(
    *,
    grammar: Mapping[str, str],
    interpolation_surface: Mapping[str, object],
) -> dict[str, object]:
    """Build fresh JSON-compatible scanner metadata from neutral inputs."""
    return {
        "version": 1,
        "applicability": {
            "publication": [{
                "profile": "archon-2026-07",
                "normalizer_versions": [6],
                "contract_reader_version": 3,
            }],
            "inherited_strict_caller_versions": [3, 4, 5, 6],
            "previous_output_versions": [6],
            "unsupported_consumer_action": "reject-capability-activation",
        },
        "grammar": {
            **dict(grammar),
            "ordinary_syntax": "$ID.output(.path)*",
            "previous_syntax": "$LOOP_PREV.ID.output(.path)*",
            "previous_prefix": "$LOOP_PREV.",
            "named_scalar": r"\$[A-Z][A-Z0-9_]*",
            "positional_scalar": r"\$[1-9][0-9]*",
        },
        "boundaries": {
            "candidate_end": {
                "text": {
                    "characters": " \t\r\n'\"(){}<>=!&|,;:",
                    "following_dollar": "not-a-delimiter",
                },
                "bash": {
                    "characters": "$ \t\r\n'\"(){}<>=!&|,;:",
                    "following_dollar": "delimiter",
                },
            },
            "reference_like_candidate": {
                "marker_suffix": {
                    ".output": "any",
                    "/output": "end-or-one-of-.[]/\\",
                    "\\output": "end-or-one-of-.[]/\\",
                },
                "first_character": {
                    "iter_output_reference_candidate_spans": (
                        "underscore-or-alphanumeric-or-non-ascii"
                    ),
                    "iter_output_references": "unfiltered",
                },
            },
            "complete_reference_suffix": {
                "reject_ascii_characters": ".[\\/-_",
                "reject_ascii_alphanumeric": True,
                "reject_every_non_ascii": True,
                "otherwise": "complete",
            },
            "malformed_suffixes": [
                ".outputx",
                ".output_",
                ".output/path",
                ".output[0]",
                ".output.01",
                ".output\\field",
                ".output-field",
                ".outputé",
            ],
            "stages": [
                "candidate-discovery",
                "lexical-admission-when-applicable",
                "strict-parsing",
                "complete-reference-presence-when-requested",
            ],
        },
        "offsets": {
            "unit": "unicode-code-point",
            "range": "half-open",
            "coordinate_space": "authored-input",
            "replacement_effect": "none",
            "utf16_conversion": "studio-editor-boundary-only",
        },
        "unicode_profiles": {
            "profiles": [
                {"id": "python-3.11-unicode-14.0.0", "python": "3.11", "unicode_database": "14.0.0"},
                {"id": "python-3.12-unicode-15.0.0", "python": "3.12", "unicode_database": "15.0.0"},
                {"id": "python-3.13-unicode-15.1.0", "python": "3.13", "unicode_database": "15.1.0"},
            ],
            "predicates": {
                "alphabetic": {
                    "python": "str.isalpha",
                    "unicode_definition": "general-category-Lm-Lt-Lu-Ll-Lo",
                },
                "digit": {
                    "python": "str.isdigit",
                    "unicode_definition": "numeric-type-Digit-or-Decimal",
                },
                "alphanumeric": {
                    "python": "str.isalnum",
                    "unicode_definition": "alphabetic-or-decimal-or-digit-or-numeric",
                },
                "whitespace": {
                    "python": "str.isspace",
                    "unicode_definition": "general-category-Zs-or-bidi-WS-B-S",
                },
            },
            "uses": {
                "reference_candidate_first": ["alphanumeric", "non-ascii"],
                "complete_reference_suffix": ["alphanumeric", "non-ascii"],
                "historical_condition_iterator": ["whitespace"],
                "bash_words": ["alphabetic", "alphanumeric", "digit"],
            },
            "selection": "explicit-profile-id",
            "unknown_profile": "unsupported",
            "published_inputs": "unicode-scalar-values-only",
            "direct_python_api_lone_surrogates": "runtime-characterization-only",
        },
        "modes": {
            "text": {
                "candidate_discovery": "left-to-right-dollar-search",
                "lexical_admission": "none",
                "strict_parsing": "on-iterator-consumption",
                "previous_outputs": "dedicated-caller-only",
            },
            "condition-v3": {
                "parser": "bounded-v3-condition",
                "previous_outputs": False,
                "reference_operands": "lhs-and-equality-reference-rhs",
            },
            "condition-v6": {
                "parser": "bounded-v3-condition-with-previous-output-operands",
                "previous_outputs": True,
                "reference_operands": "lhs-and-equality-reference-rhs",
            },
            "body-when": {
                "syntax": "condition-v6",
                "scoped_scan": ["previous-text", "equal-length-mask", "current-text"],
            },
            "bash": {
                "classifier": "bounded-authored-source-shell-state-classifier",
                "not_a_general_parser": True,
                "phase_order": [
                    "candidate-discovery",
                    "lexical-admission",
                    "strict-reference-parsing",
                ],
                "no_candidate_validation": "checks-run;final-state-needs-candidates",
                "quote_contexts": ["unquoted", "single", "double"],
                "nesting_limit": 64,
                "state_families": [
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
                ],
                "rules": _bash_rules(),
                "heredocs": {
                    "delimiter_references": "rejected",
                    "body_references": "rejected-even-when-delimiter-quoted",
                    "multiple": "processed-in-authored-order",
                    "tab_stripped": "supported",
                    "missing_terminator": "newline-consumption:reject;EOF:needs-candidates",
                },
                "malformed_precedence": {
                    "literal_candidate": "ignored-before-strict-grammar",
                    "live_candidate": "lexical-context-before-strict-grammar",
                },
            },
        },
        "scalars": {
            "pattern": r"\$(?:(?P<position>[1-9][0-9]*)|(?P<name>[A-Z][A-Z0-9_]*))",
            "names": sorted(_SCALAR_NAMES),
            "match": "maximal",
            "unknown_named": "literal",
            "positions": {"first": 1, "zero": "literal", "leading_zero": "literal"},
            "text": {"comments_and_escapes": "not-suppressed"},
            "bash": {
                "lexical_context": "classified-with-output-candidates",
                "overlap": "output-reference-wins",
                "unsafe_context": "rejected",
            },
            "arguments": {
                "split": ["shlex.split", "plain-whitespace-fallback"],
                "missing_position": "empty-string",
            },
            "replacement_rescan": False,
        },
        "callers": _callers(),
        "diagnostics": {
            "stable_native_codes": [
                "output_reference_path_unsupported",
                "output_reference_not_declared_dependency",
                "structured_output_field_impossible",
                "loop_group_scope_invalid",
                "bash_reference_context_unsupported",
                "condition_runtime_syntax_invalid",
                "named_script_output_reference_unsupported",
                "invalid_command_resource",
                "output_reference_missing",
                "output_reference_not_structured",
                "output_reference_field_missing",
                "output_reference_path_type",
                "output_reference_integrity",
                "output_reference_temporarily_unavailable",
                "output_reference_unavailable",
            ],
            "portable_scope_codes": {
                "missing_dependency": "scoped-reference-missing-dependency",
                "unknown_previous_producer": "scoped-reference-unknown-producer",
                "producer_schema_required": "scoped-reference-producer-schema-required",
                "structured_path_impossible": "scoped-reference-structured-path-impossible",
                "unknown_companion_node": "scoped-companion-reference-unknown-node",
            },
            "ordinary_missing_dependency": {
                "native": "output_reference_not_declared_dependency",
                "portable": None,
            },
            "scope_policy": {
                "previous_first_iteration_whole": "empty-string",
                "previous_first_iteration_path": "output_reference_missing",
                "unqualified_shadowing": "body-sibling-before-outer-node",
            },
            "native_by_scope": {
                "body": {
                    "missing_dependency": "loop_group_scope_invalid",
                    "unknown_previous_producer": "loop_group_scope_invalid",
                    "producer_schema_required": "loop_group_scope_invalid",
                    "structured_path_impossible": "loop_group_scope_invalid",
                },
                "group-until": {
                    "missing_dependency": "output_reference_not_declared_dependency",
                    "unknown_previous_producer": "loop_group_scope_invalid",
                    "producer_schema_required": "loop_group_scope_invalid",
                    "structured_path_impossible": "loop_group_scope_invalid",
                },
                "group-gate": {
                    "missing_dependency": "output_reference_not_declared_dependency",
                    "producer_schema_required": "output_reference_path_unsupported",
                    "structured_path_impossible": "structured_output_field_impossible",
                    "dotted_key_exception": "output_reference_path_unsupported",
                },
            },
            "cause_policy": "preserve-meaningful-reference-or-decoding-cause",
            "failure_order": "caller-native-order",
            "prose_stability": "not-a-public-identifier",
        },
        "interpolation_surface": interpolation_surface,
    }


__all__ = ["reference_scanner_contract"]
