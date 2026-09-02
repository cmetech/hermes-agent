"""Assigned prompt execution through the consumer-neutral handoff service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

from hermes_cli.handoff import (
    AgentHandoffService,
    HandoffConflict,
    HandoffEndpoint,
    HandoffNotFound,
    HandoffSnapshot,
    HandoffSpec,
)
from plugins.workflow.executors.ai import (
    render_agent_prompt,
    result_from_external_response,
)
from plugins.workflow.executors.base import NodeExecutionContext, NodeExecutionResult


_TERMINAL_PHASES = frozenset({"succeeded", "failed", "cancelled"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_thaw(item) for item in value]
    return value


class HandoffPromptExecutor:
    """Dispatch or resume one assigned Workflow prompt node."""

    def __init__(
        self,
        service: AgentHandoffService,
        assignment: Mapping[str, object],
        *,
        initiator_profile: str,
        deadline_at: datetime | None,
        utcnow: Callable[[], datetime] = _utc_now,
    ) -> None:
        if assignment.get("interaction_policy") != "deny":
            raise ValueError("assigned prompt interaction policy must be deny")
        if assignment.get("on_deadline") != "cancel_and_fail":
            raise ValueError("assigned prompt deadline policy is invalid")
        self.service = service
        self.endpoint = HandoffEndpoint.parse(assignment.get("endpoint"))
        self.initiator_profile = initiator_profile
        self.deadline_at = deadline_at
        self._utcnow = utcnow

    @staticmethod
    def _prior_handoff(context: NodeExecutionContext) -> Mapping[str, object] | None:
        value = context.node_state.get("handoff")
        return value if isinstance(value, Mapping) else None

    @staticmethod
    def _retry_authorized(
        context: NodeExecutionContext,
        prior: Mapping[str, object],
    ) -> bool:
        attempts = context.node_state.get("attempts")
        return bool(
            prior.get("last_observed_phase") in _TERMINAL_PHASES
            and isinstance(context.node_state.get("retry_consumed"), int)
            and not isinstance(context.node_state.get("retry_consumed"), bool)
            and int(context.node_state["retry_consumed"]) >= 1
            and isinstance(attempts, tuple | list)
            and len(attempts) >= 2
            and isinstance(attempts[-1], Mapping)
            and attempts[-1].get("state") == "failed"
            and any(
                isinstance(candidate, Mapping)
                and candidate.get("state") == "waiting_handoff"
                for candidate in attempts[:-1]
            )
        )

    @staticmethod
    def _semantic_key(context: NodeExecutionContext, generation: int) -> str:
        return f"{context.run_id}:{context.node.id}:{generation}"

    def _spec(
        self,
        context: NodeExecutionContext,
        *,
        prompt: str,
        generation: int,
    ) -> HandoffSpec:
        schema = (
            _thaw(context.structured_output.canonical_schema)
            if context.structured_output is not None
            else _thaw(context.node.options.get("output_format"))
        )
        capabilities = {"cancellation"}
        if schema is not None:
            capabilities.add("structured_output")
        return HandoffSpec(
            mode="task",
            endpoint=self.endpoint,
            prompt=prompt,
            output_schema=schema,
            deadline_at=self.deadline_at,
            attribution={
                "consumer": "workflow",
                "run": context.run_id,
                "node": context.node.id,
                "generation": str(generation),
            },
            required_capabilities=frozenset(capabilities),
        )

    def _load_or_create(
        self,
        context: NodeExecutionContext,
    ) -> tuple[HandoffSnapshot, int, bool]:
        prior = self._prior_handoff(context)
        retry = prior is not None and self._retry_authorized(context, prior)
        if prior is not None and not retry:
            handoff_id = prior.get("handoff_id")
            generation = prior.get("generation")
            if (
                not isinstance(handoff_id, str)
                or isinstance(generation, bool)
                or not isinstance(generation, int)
            ):
                raise ValueError("workflow handoff projection is invalid")
            snapshot = self.service.get(handoff_id)
            if (
                snapshot.handoff_id != handoff_id
                or snapshot.key_scope != self.initiator_profile
                or snapshot.handoff_key != self._semantic_key(context, generation)
                or snapshot.spec.endpoint != self.endpoint
            ):
                raise ValueError("workflow handoff identity is mismatched")
            return snapshot, generation, False

        generation = int(prior["generation"]) + 1 if retry else 1
        snapshot = self.service.create(
            self._spec(
                context,
                prompt=render_agent_prompt(context),
                generation=generation,
            ),
            self.initiator_profile,
            handoff_key=self._semantic_key(context, generation),
        )
        advanced = self.service.advance(snapshot.handoff_id, budget_seconds=2.0)
        return advanced.snapshot, generation, True

    def _metadata(
        self,
        snapshot: HandoffSnapshot,
        generation: int,
    ) -> dict[str, object]:
        return {
            "handoff_id": snapshot.handoff_id,
            "handoff_generation": generation,
            "handoff_endpoint": snapshot.spec.endpoint.canonical,
            "handoff_observed_version": snapshot.state_version,
            "handoff_observed_phase": snapshot.phase,
            "handoff_mechanism": snapshot.mechanism,
            "handoff_failure_code": snapshot.failure_code,
            "provider_attempts": 0,
            "provider_attempts_exact": True,
        }

    def _waiting_result(
        self,
        snapshot: HandoffSnapshot,
        generation: int,
    ) -> NodeExecutionResult:
        next_observation = snapshot.next_advance_at or self._utcnow()
        return NodeExecutionResult(
            "waiting_handoff",
            metadata={
                "handoff_id": snapshot.handoff_id,
                "handoff_generation": generation,
                "handoff_observed_version": snapshot.state_version,
                "handoff_observed_phase": snapshot.phase,
                "handoff_next_observation_at": next_observation.isoformat(),
                "handoff_deadline_at": (
                    snapshot.spec.deadline_at.isoformat()
                    if snapshot.spec.deadline_at is not None
                    else None
                ),
                "known_no_effect": True,
                "provider_attempts": 0,
                "provider_attempts_exact": True,
            },
        )

    def execute(self, context: NodeExecutionContext) -> NodeExecutionResult:
        if context.node.node_type != "prompt":
            return NodeExecutionResult(
                "failed",
                error_code="unsupported_handoff_node",
                error_message="only prompt nodes may be assigned",
            )
        try:
            snapshot, generation, created = self._load_or_create(context)
        except HandoffNotFound:
            return NodeExecutionResult(
                "failed",
                error_code="handoff_missing",
                error_message="assigned handoff no longer exists",
            )
        except (HandoffConflict, ValueError) as exc:
            return NodeExecutionResult(
                "failed",
                error_code="handoff_identity_mismatch",
                error_message=str(exc),
            )

        if created:
            return self._waiting_result(snapshot, generation)

        metadata = self._metadata(snapshot, generation)
        if snapshot.phase == "succeeded":
            if snapshot.terminal_result is None:
                return NodeExecutionResult(
                    "failed",
                    error_code="handoff_result_missing",
                    error_message="successful handoff has no terminal result",
                    metadata=metadata,
                )
            result = result_from_external_response(
                context,
                str(snapshot.terminal_result["text"]),
                metadata,
            )
            if result.status != "succeeded":
                return replace(result, metadata={**metadata, **result.metadata})
            return result
        if snapshot.phase in {"failed", "cancelled"}:
            return NodeExecutionResult(
                "failed",
                error_code=f"handoff_remote_{snapshot.phase}",
                error_message=f"assigned handoff {snapshot.phase}",
                metadata=metadata,
            )
        if snapshot.phase in _TERMINAL_PHASES:
            raise AssertionError("unhandled terminal handoff phase")
        return self._waiting_result(snapshot, generation)


__all__ = ["HandoffPromptExecutor"]
