"""Authenticated immediate Gateway commands for workflow admission and gates."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex

from hermes_cli.plugin_invocation import PluginInvocationContext
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.compat import (
    WorkflowCompatibilityBlockedError,
    assess_compatibility,
    require_runnable,
)
from plugins.workflow.models import RunExecutionLimits
from plugins.workflow.provenance import TriggerProvenance
from plugins.workflow.trust import (
    WorkflowTrustStore,
    build_risk_summary,
    compute_package_digest,
    preflight_execution,
)


def _verified_gateway(invocation: PluginInvocationContext) -> None:
    if (
        invocation.boundary != "gateway"
        or invocation.assurance != "verified_adapter"
    ):
        raise PermissionError("workflow Gateway commands require a verified adapter")


def gateway_trigger_provenance(
    invocation: PluginInvocationContext, *, intent_key: str
) -> TriggerProvenance:
    """Translate immutable host facts into durable workflow provenance."""
    _verified_gateway(invocation)
    if invocation.return_route_capability is None:
        raise PermissionError("workflow Gateway starts require a return route")
    return TriggerProvenance(
        source="chat",
        assurance="verified_adapter",
        intent_key=intent_key,
        source_instance=":".join(invocation.principal.split(":")[:2]),
        actor_id=invocation.principal,
        return_route=invocation.return_route_capability,
    )


def decide_gateway_run(
    store,
    *,
    decision: str,
    run_id: str,
    interaction_id: str,
    expected_version: int,
    response: str,
    invocation: PluginInvocationContext,
):
    """Apply a gate decision with adapter-established actor and scope facts."""
    _verified_gateway(invocation)
    common = {
        "expected_state_version": expected_version,
        "interaction_id": interaction_id,
        "actor": invocation.principal,
        "channel": "gateway",
        "operator_scope": invocation.operator_scope,
    }
    if decision == "approved":
        return store.approve_run(run_id, comment=response, **common)
    if decision == "rejected":
        return store.reject_run(run_id, reason=response, **common)
    raise ValueError("unknown Gateway gate decision")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="/workflow", add_help=False)
    actions = parser.add_subparsers(dest="action", required=True)
    run = actions.add_parser("run", add_help=False)
    run.add_argument("name")
    run.add_argument("--arguments", default="")
    run.add_argument("--idempotency-key", required=True)
    run.add_argument("--concurrency-key")
    for action in ("approve", "reject"):
        gate = actions.add_parser(action, add_help=False)
        gate.add_argument("run_id")
        gate.add_argument("--interaction-id", required=True)
        gate.add_argument("--expected-version", type=int, required=True)
        gate.add_argument("--comment" if action == "approve" else "--reason", default="")
    return parser


def _start_gateway_run(args, invocation, *, hermes_home: Path, workdir: Path):
    from plugins.workflow.cli import (
        _admission_compilation,
        _admission_package_digest,
        _resolve_compilation,
        _runtime_config,
        _store,
    )
    from plugins.workflow.coordinator_store import CoordinatorStore

    command_args = argparse.Namespace(
        hermes_home=str(hermes_home), workdir=str(workdir), name=args.name
    )
    compilation = _resolve_compilation(command_args, args.name)
    package = compilation.package
    runtime = _runtime_config(hermes_home, sidecar=package.sidecar)
    digest = _admission_package_digest(compilation)
    compatibility = assess_compatibility(package)
    require_runnable(compatibility)
    risk = build_risk_summary(
        package,
        compatibility,
        compilation=_admission_compilation(compilation),
    )
    if WorkflowTrustStore(hermes_home).check(
        digest.sha256, risk_digest=risk.risk_digest
    ) != "trusted":
        raise PermissionError("workflow package is not trusted")
    preflight_execution(risk, trusted=True)
    store = _store(command_args, runtime)
    coordinator = CoordinatorStore(store.database).health(
        now=datetime.now(timezone.utc)
    )
    if coordinator.status != "healthy":
        raise RuntimeError("background execution requires a healthy coordinator")
    prepared = store.prepare_run_snapshot(
        package,
        compilation=_admission_compilation(compilation),
        values={"arguments": args.arguments} if args.arguments else None,
        trusted_package_digest=(
            digest if _admission_compilation(compilation) is not None else None
        ),
        execution_limits=RunExecutionLimits.resolve(runtime),
    )
    provenance = gateway_trigger_provenance(
        invocation, intent_key=args.idempotency_key
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="chat",
            idempotency_key=args.idempotency_key,
            idempotency_namespace=invocation.operator_scope,
            concurrency_key=args.concurrency_key
            or str(package.sidecar.get("concurrency_key") or package.definition.name),
            concurrency_policy=str(package.sidecar.get("overlap_policy") or "queue"),
            execution_mode="background",
            operator_scope=invocation.operator_scope,
            provenance=provenance,
        ),
        immutable_snapshot=prepared,
    )
    if admitted.run_id is None:
        raise RuntimeError(admitted.reason_code or "workflow admission rejected")
    status = store.get_run_status(
        admitted.run_id, operator_scope=invocation.operator_scope
    )
    return {
        "action": "run",
        "run_id": admitted.run_id,
        "status": status["status"],
        "admission_disposition": admitted.disposition,
    }


def workflow_gateway_command(
    raw_args: str,
    invocation: PluginInvocationContext,
    *,
    hermes_home: str | Path | None = None,
    workdir: str | Path | None = None,
) -> str:
    """Run only background admission and gate mutations, returning promptly."""
    _verified_gateway(invocation)
    try:
        args = _parser().parse_args(shlex.split(raw_args))
        if hermes_home is None:
            from hermes_constants import get_hermes_home

            home = get_hermes_home().resolve()
        else:
            home = Path(hermes_home).resolve()
        if args.action == "run":
            result = _start_gateway_run(
                args,
                invocation,
                hermes_home=home,
                workdir=Path(workdir or Path.cwd()).resolve(),
            )
        else:
            from plugins.workflow.cli import _runtime_config, _store

            command_args = argparse.Namespace(hermes_home=str(home))
            store = _store(command_args, _runtime_config(home))
            response = args.comment if args.action == "approve" else args.reason
            decision = decide_gateway_run(
                store,
                decision="approved" if args.action == "approve" else "rejected",
                run_id=args.run_id,
                interaction_id=args.interaction_id,
                expected_version=args.expected_version,
                response=response,
                invocation=invocation,
            )
            result = {"action": args.action, **asdict(decision)}
        return json.dumps({"ok": True, "result": result}, sort_keys=True)
    except WorkflowCompatibilityBlockedError as exc:
        return json.dumps(
            {
                "ok": False,
                "error": exc.code,
                "message": str(exc),
            },
            sort_keys=True,
        )
    except (SystemExit, Exception) as exc:
        return json.dumps(
            {
                "ok": False,
                "error": type(exc).__name__,
                "message": str(exc)[:512],
            },
            sort_keys=True,
        )


__all__ = [
    "decide_gateway_run",
    "gateway_trigger_provenance",
    "workflow_gateway_command",
]
