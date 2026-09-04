"""Deterministic authoring fixtures for the published workflow language."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from plugins.workflow.language import CURRENT_NORMALIZER_BY_PROFILE
from plugins.workflow.language_schema import workflow_authoring_contract
from plugins.workflow.models import (
    SCOPED_COMPANION_UNKNOWN_NODE_SEMANTIC_CODE,
    SCOPED_REFERENCE_MISSING_DEPENDENCY_SEMANTIC_CODE,
    SCOPED_REFERENCE_PRODUCER_SCHEMA_REQUIRED_SEMANTIC_CODE,
    SCOPED_REFERENCE_STRUCTURED_PATH_IMPOSSIBLE_SEMANTIC_CODE,
    SCOPED_REFERENCE_UNKNOWN_PRODUCER_SEMANTIC_CODE,
    WorkflowLanguageProfile,
)


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_JIRA_DEFINITION = (
    _REPOSITORY_ROOT
    / "capabilities/workflow-packages/ericsson/workflows/jira-defect-loop.yaml"
)
_JIRA_COMPANION = _JIRA_DEFINITION.with_name("jira-defect-loop.hermes.yaml")


def _yaml(value: str) -> str:
    return value.strip() + "\n"


def _diagnostic(
    hermes_code: str,
    path: str,
    *,
    scope: str = "root",
    severity: str = "error",
    blocking: bool = True,
    semantic_code: str | None = None,
) -> dict[str, object]:
    return {
        "blocking": blocking,
        "code": semantic_code or hermes_code,
        "hermes_code": hermes_code,
        "path": path,
        "scope": scope,
        "severity": severity,
    }


def _case(
    profile: WorkflowLanguageProfile,
    case_id: str,
    definition_yaml: str,
    *,
    features: Iterable[str],
    diagnostics: Iterable[dict[str, object]] = (),
    companion_yaml: str | None = None,
    projection: dict[str, object] | None = None,
    provenance: dict[str, object] | None = None,
) -> dict[str, object]:
    effective_companion = companion_yaml
    if (
        effective_companion is None
        and profile is WorkflowLanguageProfile.ARCHON_2026_07
    ):
        effective_companion = "language_compatibility: archon-2026-07\n"
    normalized_diagnostics = [
        {
            **diagnostic,
            "document": (
                "companion"
                if str(diagnostic["path"]).startswith("sidecar.")
                else "definition"
            ),
        }
        for diagnostic in diagnostics
    ]
    value: dict[str, object] = {
        "id": case_id,
        "profile": profile.value,
        "normalizer_version": CURRENT_NORMALIZER_BY_PROFILE[profile],
        "definition_yaml": definition_yaml,
        "valid": not any(
            diagnostic["blocking"] is True for diagnostic in normalized_diagnostics
        ),
        "codes": [diagnostic["code"] for diagnostic in normalized_diagnostics],
        "diagnostics": normalized_diagnostics,
        "features": sorted(set(features)),
    }
    if effective_companion is not None:
        value["companion_yaml"] = effective_companion
    if projection is not None:
        value["projection"] = projection
    if provenance is not None:
        value["provenance"] = provenance
    return value


def _definition(name: str, nodes: str, *, options: str = "") -> str:
    option_lines = f"\n{options.strip()}" if options.strip() else ""
    return _yaml(
        f"""
name: {name}
description: Conformance fixture for {name}.{option_lines}
nodes:
{nodes.rstrip()}
"""
    )


def _node_kind_cases(profile: WorkflowLanguageProfile) -> list[dict[str, object]]:
    examples = {
        "command": "    command: review",
        "prompt": "    prompt: Review the input.",
        "bash": "    bash: printf ok",
        "script": "    script: |\n      print('ok')\n    runtime: uv",
        "loop": (
            "    loop:\n"
            "      prompt: Try again.\n"
            "      until: done\n"
            "      max_iterations: 2"
        ),
        "approval": "    approval:\n      message: Continue?",
        "cancel": "    cancel: Stop the workflow.",
    }
    return [
        _case(
            profile,
            f"node-kind-{kind}-valid",
            _definition(
                f"node-kind-{kind}",
                f"  - id: {kind}-node\n{body}",
            ),
            features=("field-family:node", f"node-kind:{kind}"),
        )
        for kind, body in examples.items()
    ]


def _shared_field_family_cases(
    profile: WorkflowLanguageProfile,
) -> list[dict[str, object]]:
    profile_line = f"language_compatibility: {profile.value}\n"
    definition = _definition(
        "field-families",
        """  - id: prepare
    prompt: Prepare.
    retry: {max_attempts: 1, delay_ms: 1000, on_error: transient}
    provider: openrouter
    model: small
    allowed_tools: [read_file]
    denied_tools: [terminal]
    skills: [review]
    agents:
      reviewer:
        description: Review the result.
        prompt: Check the result.
        model: medium
        tools: [read_file]
        disallowedTools: [terminal]
        skills: [review]
        maxTurns: 2
    hooks:
      PreToolUse:
        - matcher: ^read_file$
          timeout: 30
          response:
            continue: true
            systemMessage: Keep the operation bounded.
            hookSpecificOutput:
              hookEventName: PreToolUse
              permissionDecision: allow
              permissionDecisionReason: Fixture policy.
              updatedInput: {path: safe.txt}
              additionalContext: Fixture context.
  - id: repeat
    loop:
      prompt: Try again.
      until: done
      max_iterations: 2
  - id: approve
    approval:
      message: Continue?
      capture_response: true
      on_reject:
        prompt: Revise.
        max_attempts: 2
  - id: finish
    depends_on: [prepare, repeat, approve]
    bash: printf done
    trigger_rule: all_done
    context: fresh
    always_run: true
    timeout: 1000
    artifacts: true"""
        if profile is WorkflowLanguageProfile.ARCHON_2026_07
        else """  - id: prepare
    prompt: Prepare.
    retry: {max_attempts: 1, delay_ms: 1000, on_error: transient}
    provider: openrouter
    model: small
    allowed_tools: [read_file]
    denied_tools: [terminal]
    skills: [review]
    agents:
      reviewer:
        description: Review the result.
        prompt: Check the result.
        model: medium
        tools: [read_file]
        disallowedTools: [terminal]
        skills: [review]
        maxTurns: 2
    hooks:
      PreToolUse:
        - matcher: ^read_file$
          timeout: 30
          response:
            continue: true
            systemMessage: Keep the operation bounded.
            hookSpecificOutput:
              hookEventName: PreToolUse
              permissionDecision: allow
              permissionDecisionReason: Fixture policy.
              updatedInput: {path: safe.txt}
              additionalContext: Fixture context.
  - id: repeat
    loop:
      prompt: Try again.
      until: done
      max_iterations: 2
  - id: approve
    approval:
      message: Continue?
      capture_response: true
      on_reject:
        prompt: Revise.
        max_attempts: 2
  - id: finish
    depends_on: [prepare, repeat, approve]
    bash: printf done
    trigger_rule: all_done
    context: fresh
    always_run: true
    timeout: 1""",
        options="""provider: openrouter
model: small
modelReasoningEffort: high
webSearchMode: auto
interactive: false
requires: [git]
worktree: {enabled: false}
tags: [conformance]
persist_sessions: false
effort: high
thinking: adaptive
fallbackModel: medium
betas: [fixture]
sandbox: {enabled: false}""",
    )
    features = {
        "field-family:agent",
        "field-family:approval",
        "field-family:approval-reject",
        "field-family:definition",
        "field-family:hook-entry",
        "field-family:hook-event",
        "field-family:hook-response",
        "field-family:hook-specific",
        "field-family:loop",
        "field-family:node",
        "field-family:retry",
        "node-kind:approval",
        "node-kind:bash",
        "node-kind:loop",
        "node-kind:prompt",
    }
    sidecar = _yaml(
        profile_line
        + "required_services: [git]\n"
        + "tags: [conformance]\n"
        + "outward_action_nodes: [prepare]\n"
        + "outward_action_policy: approval_required\n"
        + "overlap_policy: queue\n"
        + "pause_lane_policy: hold\n"
        + "required_secrets: [FIXTURE_TOKEN]\n"
    )
    return [
        _case(
            profile,
            "field-families-valid",
            definition,
            companion_yaml=sidecar,
            features=(*features, "field-family:sidecar"),
        )
    ]


def _group_definition(
    name: str,
    body: str,
    *,
    max_iterations: int = 1,
    group_fields: str = "",
    group_options: str = "",
    outer_nodes: str = "",
) -> str:
    fields = f"\n{group_fields.rstrip()}" if group_fields.strip() else ""
    options = f"\n{group_options.rstrip()}" if group_options.strip() else ""
    prefix = f"{outer_nodes.rstrip()}\n" if outer_nodes.strip() else ""
    return _definition(
        name,
f"""{prefix}  - id: group
    loop_group:
      until: done
      max_iterations: {max_iterations}{fields}
      nodes:
{body.rstrip()}{options}""",
    )


def _large_node_body(count: int) -> str:
    return "\n".join(
        f"        - id: n{index}\n          bash: printf {index}"
        for index in range(count)
    )


def _large_edge_body(count: int) -> str:
    rows: list[str] = []
    for index in range(count):
        rows.extend((f"        - id: n{index}", "          bash: printf ok"))
        if index:
            dependencies = ", ".join(f"n{prior}" for prior in range(index))
            rows.append(f"          depends_on: [{dependencies}]")
    return "\n".join(rows)


def _approval_work_body(count: int, attempts: int) -> str:
    return "\n".join(
        (
            f"        - id: approve{index}\n"
            "          approval:\n"
            "            message: Continue?\n"
            "            on_reject:\n"
            "              prompt: Revise.\n"
            f"              max_attempts: {attempts}"
        )
        for index in range(count)
    )


def _archon_loop_group_cases(
    profile: WorkflowLanguageProfile,
) -> list[dict[str, object]]:
    group_scope = "loop-group:group"

    def issue(
        hermes_code: str,
        path: str,
        *,
        scope: str = group_scope,
        semantic_code: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        return (
            _diagnostic(
                hermes_code,
                path,
                scope=scope,
                semantic_code=semantic_code,
            ),
        )

    cases = [
        _case(
            profile,
            "loop-group-minimal-valid",
            _group_definition(
                "loop-group-minimal",
                "        - id: work\n          prompt: Work.",
            ),
            features=("field-family:loop-group", "node-kind:loop_group"),
        ),
        _case(
            profile,
            "loop-group-all-body-kinds-valid",
            _group_definition(
                "loop-group-all-body-kinds",
                """        - id: command
          command: review
        - id: prompt
          prompt: Work.
        - id: bash
          bash: printf ok
        - id: script
          script: |
            print('ok')
          runtime: uv
        - id: loop
          loop:
            prompt: Retry.
            until: done
            max_iterations: 1
        - id: approval
          approval:
            message: Continue?
        - id: cancel
          cancel: Stop.""",
            ),
            features=(
                "field-family:loop-group",
                "node-kind:approval",
                "node-kind:bash",
                "node-kind:cancel",
                "node-kind:command",
                "node-kind:loop",
                "node-kind:loop_group",
                "node-kind:prompt",
                "node-kind:script",
            ),
        ),
        _case(
            profile,
            "loop-group-empty-body",
            _group_definition("loop-group-empty", "        []"),
            diagnostics=issue(
                "loop_group_shape_invalid", "nodes[0].loop_group.nodes"
            ),
            features=("field-family:loop-group", "invalid:empty-body"),
        ),
        _case(
            profile,
            "loop-group-duplicate-id",
            _group_definition(
                "loop-group-duplicate-id",
                """        - id: same
          prompt: One.
        - id: same
          prompt: Two.""",
            ),
            diagnostics=issue(
                "loop_group_topology_invalid",
                "nodes[0].loop_group.nodes[1].id",
            ),
            features=("invalid:duplicate-id",),
        ),
        _case(
            profile,
            "loop-group-missing-dependency",
            _group_definition(
                "loop-group-missing-dependency",
                """        - id: consumer
          depends_on: [missing]
          prompt: Consume.""",
            ),
            diagnostics=issue(
                "loop_group_scope_invalid",
                "nodes[0].loop_group.nodes[0].depends_on",
            ),
            features=("invalid:missing-dependency",),
        ),
        _case(
            profile,
            "loop-group-self-edge",
            _group_definition(
                "loop-group-self-edge",
                """        - id: self
          depends_on: [self]
          prompt: Consume.""",
            ),
            diagnostics=issue(
                "loop_group_topology_invalid", "nodes[0].loop_group.nodes"
            ),
            features=("invalid:self-edge",),
        ),
        _case(
            profile,
            "loop-group-cycle",
            _group_definition(
                "loop-group-cycle",
                """        - id: one
          depends_on: [two]
          prompt: One.
        - id: two
          depends_on: [one]
          prompt: Two.""",
            ),
            diagnostics=issue(
                "loop_group_topology_invalid", "nodes[0].loop_group.nodes"
            ),
            features=("invalid:cycle",),
        ),
        _case(
            profile,
            "loop-group-too-many-nodes",
            _group_definition(
                "loop-group-too-many-nodes", _large_node_body(513)
            ),
            diagnostics=issue(
                "loop_group_product_limit", "nodes[0].loop_group.nodes"
            ),
            features=("boundary:nodes-over",),
        ),
        _case(
            profile,
            "loop-group-too-many-edges",
            _group_definition(
                "loop-group-too-many-edges", _large_edge_body(92)
            ),
            diagnostics=issue(
                "loop_group_product_limit", "nodes[0].loop_group.nodes"
            ),
            features=("boundary:edges-over",),
        ),
        _case(
            profile,
            "loop-group-forbidden-include",
            _group_definition(
                "loop-group-forbidden-include",
                "        - id: child\n          include: child-workflow",
            ),
            diagnostics=issue(
                "loop_group_shape_invalid",
                "nodes[0].loop_group.nodes[0].include",
            ),
            features=("invalid:forbidden-include",),
        ),
        _case(
            profile,
            "loop-group-forbidden-workflow",
            _group_definition(
                "loop-group-forbidden-workflow",
                "        - id: child\n          workflow: child-workflow",
            ),
            diagnostics=issue(
                "loop_group_shape_invalid",
                "nodes[0].loop_group.nodes[0].workflow",
            ),
            features=("invalid:forbidden-workflow",),
        ),
        _case(
            profile,
            "loop-group-forbidden-nested-group",
            _group_definition(
                "loop-group-forbidden-nested-group",
                """        - id: nested
          loop_group:
            until: done
            max_iterations: 1
            nodes:
              - id: child
                prompt: Work.""",
            ),
            diagnostics=issue(
                "loop_group_shape_invalid", "nodes[0].loop_group.nodes[0]"
            ),
            features=("invalid:nested-group",),
        ),
        _case(
            profile,
            "loop-group-forbidden-retry",
            _group_definition(
                "loop-group-forbidden-retry",
                "        - id: work\n          prompt: Work.",
                group_options="    retry: {max_attempts: 1}",
            ),
            diagnostics=issue(
                "loop_group_shape_invalid", "nodes[0].retry", scope="root"
            ),
            features=("invalid:group-retry",),
        ),
        _case(
            profile,
            "loop-group-current-ref-with-dependency",
            _group_definition(
                "loop-group-current-ref-with-dependency",
                """        - id: producer
          prompt: Produce.
        - id: consumer
          depends_on: [producer]
          prompt: Use $producer.output""",
            ),
            features=("reference:current-body",),
        ),
        _case(
            profile,
            "loop-group-current-ref-needs-dependency",
            _group_definition(
                "loop-group-current-ref-needs-dependency",
                """        - id: producer
          prompt: Produce.
        - id: consumer
          prompt: Use $producer.output""",
            ),
            diagnostics=issue(
                "loop_group_scope_invalid",
                "nodes[0].loop_group.nodes[1].prompt",
                semantic_code=(
                    SCOPED_REFERENCE_MISSING_DEPENDENCY_SEMANTIC_CODE
                ),
            ),
            features=("reference:current-body", "invalid:missing-dependency"),
        ),
        _case(
            profile,
            "loop-group-mixed-ref-needs-dependency",
            _group_definition(
                "loop-group-mixed-ref-needs-dependency",
                """        - id: producer
          prompt: Produce.
        - id: consumer
          prompt: Use $LOOP_PREV.producer.output and $producer.output""",
            ),
            diagnostics=issue(
                "loop_group_scope_invalid",
                "nodes[0].loop_group.nodes[1].prompt",
                semantic_code=(
                    SCOPED_REFERENCE_MISSING_DEPENDENCY_SEMANTIC_CODE
                ),
            ),
            features=(
                "reference:current-body",
                "reference:loop-prev",
                "invalid:missing-dependency",
            ),
        ),
        _case(
            profile,
            "loop-group-outer-ref-with-dependency",
            _group_definition(
                "loop-group-outer-ref-with-dependency",
                "        - id: consumer\n          prompt: Use $outer.output",
                outer_nodes="  - id: outer\n    prompt: Produce.",
                group_options="    depends_on: [outer]",
            ),
            features=("reference:outer",),
        ),
        _case(
            profile,
            "loop-group-outer-ref-needs-dependency",
            _group_definition(
                "loop-group-outer-ref-needs-dependency",
                "        - id: consumer\n          prompt: Use $outer.output",
                outer_nodes="  - id: outer\n    prompt: Produce.",
            ),
            diagnostics=issue(
                "loop_group_scope_invalid",
                "nodes[1].loop_group.nodes[0].prompt",
                semantic_code=(
                    SCOPED_REFERENCE_MISSING_DEPENDENCY_SEMANTIC_CODE
                ),
            ),
            features=("reference:outer", "invalid:missing-dependency"),
        ),
        _case(
            profile,
            "loop-group-loop-prev-valid",
            _group_definition(
                "loop-group-loop-prev-valid",
                "        - id: producer\n          prompt: Use $LOOP_PREV.producer.output",
            ),
            features=("reference:loop-prev",),
        ),
        _case(
            profile,
            "loop-group-loop-prev-unknown-producer",
            _group_definition(
                "loop-group-loop-prev-unknown",
                "        - id: consumer\n          prompt: Use $LOOP_PREV.missing.output",
            ),
            diagnostics=issue(
                "loop_group_scope_invalid",
                "nodes[0].loop_group.nodes[0].prompt",
                semantic_code=(
                    SCOPED_REFERENCE_UNKNOWN_PRODUCER_SEMANTIC_CODE
                ),
            ),
            features=("reference:loop-prev", "invalid:unknown-producer"),
        ),
        _case(
            profile,
            "loop-group-gate-ref-needs-dependency",
            _group_definition(
                "loop-group-gate-ref-needs-dependency",
                "        - id: consumer\n          prompt: Consume.",
                outer_nodes="  - id: outer\n    prompt: Produce.",
                group_fields="      gate_message: Review $outer.output",
            ),
            diagnostics=issue(
                "output_reference_not_declared_dependency",
                "nodes[1].loop_group.gate_message",
                semantic_code=(
                    SCOPED_REFERENCE_MISSING_DEPENDENCY_SEMANTIC_CODE
                ),
            ),
            features=("reference:outer", "surface:group-gate-message"),
        ),
        _case(
            profile,
            "loop-group-structured-paths-valid",
            _group_definition(
                "loop-group-structured-paths-valid",
                """        - id: producer
          prompt: Produce.
          output_format:
            type: object
            properties:
              status: {type: string}
            additionalProperties: false
        - id: consumer
          depends_on: [producer]
          prompt: Use $producer.output.status $outer.output.status $LOOP_PREV.producer.output.status""",
                outer_nodes="""  - id: outer
    prompt: Produce.
    output_format:
      type: object
      properties:
        status: {type: string}
      additionalProperties: false""",
                group_fields="""      until_bash: test "$producer.output.status $outer.output.status $LOOP_PREV.producer.output.status" = expected
      gate_message: Review $outer.output.status""",
                group_options="    depends_on: [outer]",
            ),
            features=(
                "reference:current-body",
                "reference:loop-prev",
                "reference:outer",
                "reference:structured-path",
                "surface:group-gate-message",
                "surface:group-until-bash",
            ),
        ),
        _case(
            profile,
            "loop-group-structured-schema-required",
            _group_definition(
                "loop-group-structured-schema-required",
                """        - id: producer
          prompt: Produce.
        - id: consumer
          depends_on: [producer]
          prompt: Use $producer.output.status""",
            ),
            diagnostics=issue(
                "loop_group_scope_invalid",
                "nodes[0].loop_group.nodes[1].prompt",
                semantic_code=(
                    SCOPED_REFERENCE_PRODUCER_SCHEMA_REQUIRED_SEMANTIC_CODE
                ),
            ),
            features=(
                "invalid:producer-schema-required",
                "reference:current-body",
                "reference:structured-path",
            ),
        ),
        _case(
            profile,
            "loop-group-structured-path-impossible",
            _group_definition(
                "loop-group-structured-path-impossible",
                """        - id: producer
          prompt: Produce.
          output_format:
            type: object
            properties:
              status: {type: string}
            additionalProperties: false""",
                group_fields=(
                    "      until_bash: test -n '$LOOP_PREV.producer.output.missing'"
                ),
            ),
            diagnostics=issue(
                "loop_group_scope_invalid",
                "nodes[0].loop_group.until_bash",
                semantic_code=(
                    SCOPED_REFERENCE_STRUCTURED_PATH_IMPOSSIBLE_SEMANTIC_CODE
                ),
            ),
            features=(
                "invalid:structured-path-impossible",
                "reference:loop-prev",
                "reference:structured-path",
                "surface:group-until-bash",
            ),
        ),
        _case(
            profile,
            "loop-group-promoted-schema-valid",
            _group_definition(
                "loop-group-promoted-schema-valid",
                "        - id: consume\n          prompt: Use $producer-group.output.status",
                outer_nodes="""  - id: producer-group
    loop_group:
      until: done
      max_iterations: 1
      nodes:
        - id: first-terminal
          prompt: Produce.
          output_format:
            type: object
            properties:
              status: {type: string}
            additionalProperties: false
        - id: second-terminal
          prompt: Not promoted.
          output_format:
            type: object
            properties:
              other: {type: string}
            additionalProperties: false""",
                group_fields=(
                    "      gate_message: Review $producer-group.output.status"
                ),
                group_options="    depends_on: [producer-group]",
            ),
            features=(
                "reference:outer",
                "reference:structured-path",
                "surface:group-gate-message",
            ),
        ),
        _case(
            profile,
            "loop-group-promoted-schema-body-impossible",
            _group_definition(
                "loop-group-promoted-schema-body-impossible",
                "        - id: consume\n          prompt: Use $producer-group.output.missing",
                outer_nodes="""  - id: producer-group
    loop_group:
      until: done
      max_iterations: 1
      nodes:
        - id: first-terminal
          prompt: Produce.
          output_format:
            type: object
            properties:
              status: {type: string}
            additionalProperties: false""",
                group_options="    depends_on: [producer-group]",
            ),
            diagnostics=issue(
                "loop_group_scope_invalid",
                "nodes[1].loop_group.nodes[0].prompt",
                semantic_code=(
                    SCOPED_REFERENCE_STRUCTURED_PATH_IMPOSSIBLE_SEMANTIC_CODE
                ),
            ),
            features=(
                "invalid:structured-path-impossible",
                "reference:outer",
                "reference:structured-path",
            ),
        ),
        _case(
            profile,
            "loop-group-promoted-schema-gate-impossible",
            _group_definition(
                "loop-group-promoted-schema-gate-impossible",
                "        - id: consume\n          prompt: Consume.",
                outer_nodes="""  - id: producer-group
    loop_group:
      until: done
      max_iterations: 1
      nodes:
        - id: first-terminal
          prompt: Produce.
          output_format:
            type: object
            properties:
              status: {type: string}
            additionalProperties: false""",
                group_fields=(
                    "      gate_message: Review $producer-group.output.missing"
                ),
                group_options="    depends_on: [producer-group]",
            ),
            diagnostics=issue(
                "structured_output_field_impossible",
                "nodes[1].loop_group.gate_message",
                semantic_code=(
                    SCOPED_REFERENCE_STRUCTURED_PATH_IMPOSSIBLE_SEMANTIC_CODE
                ),
            ),
            features=(
                "invalid:structured-path-impossible",
                "reference:outer",
                "reference:structured-path",
                "surface:group-gate-message",
            ),
        ),
        _case(
            profile,
            "loop-group-promoted-schema-unsupported-conservative",
            _group_definition(
                "loop-group-promoted-schema-unsupported-conservative",
                "        - id: consume\n          prompt: Use $producer-group.output.forbidden",
                outer_nodes="""  - id: producer-group
    loop_group:
      until: done
      max_iterations: 1
      nodes:
        - id: first-terminal
          prompt: Produce.
          output_format:
            type: object
            not:
              required: [forbidden]""",
                group_options="    depends_on: [producer-group]",
            ),
            features=(
                "reference:outer",
                "reference:structured-path",
                "schema-proof:unsupported-conservative",
            ),
        ),
        _case(
            profile,
            "loop-group-first-terminal-primary",
            _group_definition(
                "loop-group-primary-sink",
                """        - id: first-terminal
          prompt: First.
        - id: second-terminal
          prompt: Second.""",
            ),
            features=("projection:primary-sink",),
            projection={
                "group_id": "group",
                "primary_sink": "first-terminal",
                "scoped_node_ids": [
                    "group/first-terminal",
                    "group/second-terminal",
                ],
            },
        ),
        _case(
            profile,
            "loop-group-companion-child-reference-valid",
            _group_definition(
                "loop-group-companion-reference",
                "        - id: child\n          prompt: Act.",
            ),
            companion_yaml=_yaml(
                """language_compatibility: archon-2026-07
outward_action_nodes: [group/child]
outward_action_policy: approval_required"""
            ),
            features=("field-family:sidecar", "reference:companion-group-child"),
        ),
        _case(
            profile,
            "loop-group-companion-child-reference-unknown",
            _group_definition(
                "loop-group-companion-reference-unknown",
                "        - id: child\n          prompt: Act.",
            ),
            companion_yaml=_yaml(
                """language_compatibility: archon-2026-07
outward_action_nodes: [group/missing]
outward_action_policy: approval_required"""
            ),
            diagnostics=issue(
                "unknown_sidecar_node",
                "sidecar.outward_action_nodes",
                semantic_code=(
                    SCOPED_COMPANION_UNKNOWN_NODE_SEMANTIC_CODE
                ),
            ),
            features=("field-family:sidecar", "reference:companion-group-child"),
        ),
        _case(
            profile,
            "loop-group-work-product-boundary",
            _group_definition(
                "loop-group-work-boundary",
                _approval_work_body(8, 7),
                max_iterations=64,
            ),
            features=("boundary:work-product",),
        ),
        _case(
            profile,
            "loop-group-work-one-over",
            _group_definition(
                "loop-group-work-one-over",
                "\n".join((
                    *(
                        f"        - id: prompt{index}\n          prompt: Work."
                        for index in range(80)
                    ),
                    "        - id: cancel\n          cancel: Stop.",
                )),
                max_iterations=17,
            ),
            diagnostics=issue(
                "loop_group_product_limit", "nodes[0].loop_group"
            ),
            features=("boundary:work-product-over",),
            projection={"child_attempts": 4_097},
        ),
        _case(
            profile,
            "loop-group-unknown-field-preserved",
            _group_definition(
                "loop-group-unknown-field-preserved",
                "        - id: work\n          prompt: Work.",
                group_fields=(
                    "      future_editor_field: {preserve: exactly}"
                ),
            ),
            diagnostics=issue(
                "loop_group_shape_invalid",
                "nodes[0].loop_group.future_editor_field",
            ),
            features=("field-family:loop-group", "preservation:unknown-field"),
        ),
    ]

    jira_definition = _JIRA_DEFINITION.read_text(encoding="utf-8")
    jira_companion = _JIRA_COMPANION.read_text(encoding="utf-8")
    cases.append(
        _case(
            profile,
            "jira-defect-loop-distributed",
            jira_definition,
            companion_yaml=jira_companion,
            features=(
                "field-family:loop-group",
                "field-family:sidecar",
                "provenance:distributed",
                "reference:companion-group-child",
                "reference:current-body",
                "reference:loop-prev",
                "reference:outer",
            ),
            provenance={
                "kind": "distributed-workflow-package",
                "definition": (
                    "capabilities/workflow-packages/ericsson/workflows/"
                    "jira-defect-loop.yaml"
                ),
                "companion": (
                    "capabilities/workflow-packages/ericsson/workflows/"
                    "jira-defect-loop.hermes.yaml"
                ),
            },
        )
    )
    return cases


def _legacy_specific_cases(
    profile: WorkflowLanguageProfile,
) -> list[dict[str, object]]:
    return [
        _case(
            profile,
            "legacy-loop-group-version-rejected",
            _group_definition(
                "legacy-loop-group",
                "        - id: child\n          prompt: Work.",
            ),
            diagnostics=(
                _diagnostic(
                    "loop_group_version_unsupported", "nodes[0].loop_group"
                ),
            ),
            features=("legacy:v6-rejected", "node-kind:loop_group"),
        ),
        _case(
            profile,
            "legacy-artifacts-version-rejected",
            _definition(
                "legacy-artifacts",
                """  - id: process
    bash: printf ok
    artifacts: false""",
            ),
            diagnostics=(
                _diagnostic(
                    "artifacts_version_unsupported", "nodes[0].artifacts"
                ),
            ),
            features=("legacy:v6-rejected",),
        ),
        _case(
            profile,
            "legacy-unknown-top-level-preserved",
            _definition(
                "legacy-unknown-field",
                "  - id: work\n    prompt: Work.",
                options="future_editor_field: {preserve: exactly}",
            ),
            diagnostics=(
                _diagnostic(
                    "unknown_top_level_field",
                    "future_editor_field",
                    severity="warning",
                    blocking=False,
                ),
            ),
            features=("preservation:unknown-field",),
        ),
    ]


def workflow_language_conformance(
    profile: WorkflowLanguageProfile,
) -> dict[str, object]:
    """Return the bounded authoring corpus for one exact language profile."""
    selected = WorkflowLanguageProfile(profile)
    contract = workflow_authoring_contract(selected)
    cases = [
        *_node_kind_cases(selected),
        *_shared_field_family_cases(selected),
    ]
    if selected is WorkflowLanguageProfile.ARCHON_2026_07:
        cases.extend(_archon_loop_group_cases(selected))
    else:
        cases.extend(_legacy_specific_cases(selected))
    return {
        "format_version": 1,
        "profile": selected.value,
        "normalizer_version": CURRENT_NORMALIZER_BY_PROFILE[selected],
        "contract": {
            "schema_version": contract["schema_version"],
            "contract_reader_version": contract["contract_reader_version"],
            "contract_digest": contract["contract_digest"],
            "normalizer": "plugins.workflow.language.normalize_workflow",
            "validator": (
                "plugins.workflow.schema._compile_workflow_source_document"
            ),
        },
        "cases": cases,
        "x-hermes-provenance": {
            "producer": "hermes-agent",
            "command": (
                f"hermes workflow schema-corpus --profile {selected.value} --json"
            ),
            "fixture_authority": "plugins.workflow.language_conformance",
        },
    }
