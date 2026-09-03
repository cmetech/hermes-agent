"""Authenticated registered-peer transport for durable Hermes Runs."""

from __future__ import annotations

from pathlib import Path
import urllib.error

from hermes_cli.peers import (
    ResolvedPeer,
    ensure_peer_bot_chat,
    peer_dm_request,
    resolve_peer,
)

from .models import ChannelObservation, HandoffEndpoint, HandoffSnapshot
from .runs import (
    RunsClient,
    RunsConnection,
    RunsDeadline,
    advertised_capabilities,
    deliver_run_command,
    observation_from_status,
    terminal_result,
)
from .service import (
    ChannelDefinitelyNotAccepted,
    ChannelIndeterminate,
    ChannelRetryableFailure,
    EndpointAssessment,
)


_BASE_CAPABILITIES = frozenset({
    "authoritative_status",
    "cancellation",
    "durable_admission",
})


def _failure_code(exc: urllib.error.HTTPError) -> str:
    if exc.code in {401, 403}:
        return "peer_auth_rejected"
    if exc.code == 404:
        return "peer_profile_not_found"
    return f"http_{int(exc.code)}"


class PeerHermesChannel:
    """Handoff channel resolved only through one initiating profile registry."""

    def __init__(self, initiating_home: Path | str) -> None:
        self._initiating_home = Path(initiating_home).expanduser().resolve()

    def _resolve(
        self, endpoint: HandoffEndpoint
    ) -> tuple[ResolvedPeer | None, str | None]:
        if endpoint.kind != "peer" or endpoint.peer is None:
            return None, "endpoint_invalid"
        try:
            return (
                resolve_peer(
                    endpoint.peer,
                    endpoint.profile,
                    initiating_home=self._initiating_home,
                ),
                None,
            )
        except LookupError:
            return None, "peer_not_found"
        except PermissionError:
            return None, "peer_auth_unavailable"
        except ValueError:
            return None, "peer_registry_invalid"

    def _assess(
        self, endpoint: HandoffEndpoint, deadline: RunsDeadline
    ) -> tuple[ResolvedPeer | None, frozenset[str], str | None]:
        resolved, failure = self._resolve(endpoint)
        if resolved is None:
            return None, frozenset(), failure
        try:
            document = RunsClient(
                RunsConnection(resolved.profile_base_url, resolved.key), deadline
            ).request_json("/v1/capabilities")
        except urllib.error.HTTPError as exc:
            return None, frozenset(), _failure_code(exc)
        except (OSError, TimeoutError, ValueError):
            return None, frozenset(), "endpoint_unavailable"
        capabilities, failure = advertised_capabilities(document)
        return (resolved if failure is None else None), capabilities, failure

    def validate_endpoint(
        self, endpoint: HandoffEndpoint, _initiator: str
    ) -> EndpointAssessment:
        _resolved, capabilities, failure = self._assess(
            endpoint, RunsDeadline(2)
        )
        return EndpointAssessment(
            endpoint=endpoint,
            available=failure is None,
            mechanism="peer_runs" if failure is None else None,
            failure_code=failure,
            capabilities=capabilities,
        )

    def bind(
        self, snapshot: HandoffSnapshot, *, budget_seconds: float
    ) -> ChannelObservation:
        deadline = RunsDeadline(budget_seconds)
        legacy_conversation = (
            snapshot.spec.mode == "conversation"
            and not snapshot.spec.required_capabilities
        )
        if legacy_conversation:
            resolved, failure = self._resolve(snapshot.spec.endpoint)
            capabilities = frozenset()
        else:
            resolved, capabilities, failure = self._assess(
                snapshot.spec.endpoint, deadline
            )
        if resolved is None:
            return ChannelObservation(phase="failed", failure_code=failure)
        client = RunsClient(
            RunsConnection(resolved.profile_base_url, resolved.key), deadline
        )
        if legacy_conversation:
            try:
                session_id = ensure_peer_bot_chat(client)
            except urllib.error.HTTPError as exc:
                return ChannelObservation(
                    phase="failed", failure_code=_failure_code(exc)
                )
            except (OSError, TimeoutError, ValueError):
                return ChannelObservation(
                    phase="prepared", failure_code="endpoint_unavailable"
                )
            return ChannelObservation(
                phase="prepared",
                mechanism="peer_dm",
                binding={
                    "peer": resolved.name,
                    "profile": resolved.profile,
                    "mechanism": "peer_dm",
                    "capabilities": [],
                    "origin_sha256": resolved.origin_sha256,
                    "auth_scope_sha256": resolved.auth_scope_sha256,
                },
                checkpoint={"session_id": session_id},
            )
        required = _BASE_CAPABILITIES | (
            snapshot.spec.required_capabilities - {"structured_output"}
        )
        if not required <= capabilities:
            return ChannelObservation(
                phase="failed", failure_code="capability_mismatch"
            )
        checkpoint = {}
        if snapshot.spec.mode == "conversation":
            try:
                checkpoint = {"session_id": client.ensure_session(
                    "Bot Chat", source="bot_handoff"
                )}
            except urllib.error.HTTPError as exc:
                return ChannelObservation(
                    phase="failed", failure_code=_failure_code(exc)
                )
            except (OSError, TimeoutError, ValueError):
                return ChannelObservation(
                    phase="prepared", failure_code="endpoint_unavailable"
                )
        return ChannelObservation(
            phase="prepared",
            mechanism="peer_runs",
            binding={
                "peer": resolved.name,
                "profile": resolved.profile,
                "mechanism": "peer_runs",
                "capabilities": sorted(capabilities),
                "origin_sha256": resolved.origin_sha256,
                "auth_scope_sha256": resolved.auth_scope_sha256,
            },
            checkpoint=checkpoint,
        )

    def _bound_client(
        self, snapshot: HandoffSnapshot, deadline: RunsDeadline
    ) -> RunsClient:
        binding = snapshot.binding or {}
        endpoint = snapshot.spec.endpoint
        resolved, failure = self._resolve(endpoint)
        if resolved is None:
            raise ChannelIndeterminate(failure or "endpoint_unavailable")
        if (
            binding.get("peer") != resolved.name
            or binding.get("profile") != resolved.profile
            or binding.get("origin_sha256") != resolved.origin_sha256
            or binding.get("auth_scope_sha256") != resolved.auth_scope_sha256
        ):
            raise ChannelIndeterminate("peer_binding_changed")
        return RunsClient(
            RunsConnection(resolved.profile_base_url, resolved.key), deadline
        )

    def _submit(
        self, snapshot: HandoffSnapshot, deadline: RunsDeadline
    ) -> ChannelObservation:
        client = self._bound_client(snapshot, deadline)
        session_id = str((snapshot.checkpoint or {}).get("session_id") or "")
        if snapshot.mechanism == "peer_dm":
            if not session_id:
                raise ChannelIndeterminate("session_missing")
            try:
                response = peer_dm_request(
                    client, snapshot.spec.prompt, session_id=session_id
                )
            except urllib.error.HTTPError as exc:
                if 400 <= exc.code < 500:
                    raise ChannelDefinitelyNotAccepted(_failure_code(exc)) from exc
                raise ChannelIndeterminate() from exc
            except (OSError, TimeoutError, ValueError) as exc:
                raise ChannelIndeterminate() from exc
            return ChannelObservation(
                phase="succeeded",
                checkpoint={"session_id": response["session_id"], "status": "completed"},
                terminal_result=terminal_result(response["reply"]),
            )
        try:
            response = client.submit(
                handoff_id=snapshot.handoff_id,
                prompt=snapshot.spec.prompt,
                session_id=session_id or None,
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                raise ChannelDefinitelyNotAccepted(
                    "idempotency_key_conflict"
                ) from exc
            if 400 <= exc.code < 500:
                raise ChannelDefinitelyNotAccepted(_failure_code(exc)) from exc
            raise ChannelIndeterminate() from exc
        except (OSError, TimeoutError, ValueError) as exc:
            raise ChannelIndeterminate() from exc
        checkpoint = {
            "run_id": response["run_id"],
            "idempotency_key": response["idempotency_key"],
            "status": response["status"],
        }
        if session_id:
            checkpoint["session_id"] = session_id
        return ChannelObservation(
            phase="submitted",
            checkpoint=checkpoint,
        )

    def submit(
        self, snapshot: HandoffSnapshot, *, budget_seconds: float
    ) -> ChannelObservation:
        return self._submit(snapshot, RunsDeadline(budget_seconds))

    def _observe(
        self,
        snapshot: HandoffSnapshot,
        deadline: RunsDeadline,
        *,
        cancelling: bool = False,
    ) -> ChannelObservation:
        client = self._bound_client(snapshot, deadline)
        run_id = str((snapshot.checkpoint or {}).get("run_id") or "")
        if not run_id:
            raise ChannelIndeterminate()
        try:
            response = client.status(run_id)
        except urllib.error.HTTPError as exc:
            if exc.code >= 500:
                raise ChannelRetryableFailure() from exc
            raise ChannelIndeterminate() from exc
        except (OSError, TimeoutError, ValueError) as exc:
            raise ChannelRetryableFailure() from exc
        try:
            observation = observation_from_status(
                snapshot, response, cancelling=cancelling
            )
            return ChannelObservation(
                phase=observation.phase,
                checkpoint=observation.checkpoint,
                terminal_result=observation.terminal_result,
                failure_code=observation.failure_code,
            )
        except (TypeError, UnicodeError, ValueError) as exc:
            raise ChannelIndeterminate() from exc

    def deliver_command(
        self,
        snapshot: HandoffSnapshot,
        command: CommandRecord,
        *,
        budget_seconds: float,
    ) -> tuple[str, str | None]:
        deadline = RunsDeadline(budget_seconds)
        client = self._bound_client(snapshot, deadline)
        return deliver_run_command(client, snapshot, command)

    def reconcile(
        self, snapshot: HandoffSnapshot, *, budget_seconds: float
    ) -> ChannelObservation:
        if snapshot.mechanism == "peer_dm":
            return ChannelObservation(
                phase="indeterminate",
                checkpoint=snapshot.checkpoint or {},
                failure_code=snapshot.failure_code or "submission_indeterminate",
            )
        deadline = RunsDeadline(budget_seconds)
        if (snapshot.checkpoint or {}).get("run_id"):
            return self._observe(
                snapshot,
                deadline,
                cancelling=snapshot.cancel_requested_at is not None,
            )
        return self._submit(snapshot, deadline)

    def observe(
        self, snapshot: HandoffSnapshot, *, budget_seconds: float
    ) -> ChannelObservation:
        if snapshot.mechanism == "peer_dm":
            return ChannelObservation(
                phase="indeterminate",
                checkpoint=snapshot.checkpoint or {},
                failure_code="observation_indeterminate",
            )
        return self._observe(snapshot, RunsDeadline(budget_seconds))

    def cancel(
        self, snapshot: HandoffSnapshot, *, budget_seconds: float
    ) -> ChannelObservation:
        if snapshot.mechanism == "peer_dm":
            return ChannelObservation(
                phase="indeterminate",
                checkpoint=snapshot.checkpoint or {},
                failure_code="cancellation_indeterminate",
            )
        deadline = RunsDeadline(budget_seconds)
        if (snapshot.checkpoint or {}).get("status") == "stopping":
            return self._observe(snapshot, deadline, cancelling=True)
        client = self._bound_client(snapshot, deadline)
        run_id = str((snapshot.checkpoint or {}).get("run_id") or "")
        if not run_id:
            raise ChannelIndeterminate()
        try:
            client.stop(run_id)
        except (urllib.error.HTTPError, OSError, TimeoutError, ValueError) as exc:
            raise ChannelIndeterminate() from exc
        checkpoint = dict(snapshot.checkpoint or {})
        checkpoint.update({"run_id": run_id, "status": "stopping"})
        return ChannelObservation(phase="cancelling", checkpoint=checkpoint)


__all__ = ["PeerHermesChannel"]
