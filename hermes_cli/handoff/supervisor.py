"""Fair host-owned advancement and return publication for agent handoffs."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import threading

from hermes_cli.plugin_services import (
    BackgroundServiceContext,
    BackgroundServiceHealth,
)
from hermes_cli.profiles import validate_profile_name

from .service import AgentHandoffService
from .store import HandoffStore, StaleAdvanceLease


logger = logging.getLogger(__name__)

_ACTIVE_PHASES = (
    "prepared",
    "submitted",
    "active",
    "needs_input",
    "cancelling",
    "indeterminate",
)
_ADVANCE_BATCH = 8
_DELIVERY_BATCH = 8
_PROFILE_BATCH = 4
_SCAN_PAGE = 8
_ADVANCE_BUDGET_SECONDS = 2.0
_DELIVERY_LEASE_SECONDS = 30.0
_DELIVERY_RETRY_SECONDS = 2.0
_TICK_SECONDS = 1.0
_MAX_AUTOMATIC_HOPS = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _profile_root(home: Path) -> Path:
    return home.parent.parent if home.parent.name == "profiles" else home


def _valid_profile_homes(
    profile_homes: Iterable[tuple[str, Path | str]],
) -> tuple[tuple[str, Path], ...]:
    entries = tuple(profile_homes)
    if not entries:
        return ()
    root = _profile_root(Path(entries[0][1]).expanduser().absolute())
    resolved_root = root.resolve(strict=False)
    resolved_profiles = (root / "profiles").resolve(strict=False)
    valid: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for profile, raw_home in entries:
        try:
            validate_profile_name(profile)
            home = Path(raw_home).expanduser().absolute()
            expected = root if profile == "default" else root / "profiles" / profile
            resolved = home.resolve(strict=True)
            if home != expected or (
                profile == "default"
                and resolved != resolved_root
                or profile != "default"
                and resolved.parent != resolved_profiles
            ):
                continue
            key = str(resolved)
            if key in seen:
                continue
        except (OSError, TypeError, ValueError):
            continue
        seen.add(key)
        valid.append((profile, resolved))
    return tuple(valid)


def _served_profile_homes(
    source_home: Path, host_kind: str
) -> tuple[tuple[str, Path], ...]:
    from gateway.config import GatewayConfig
    from hermes_cli.config import load_config_readonly
    from hermes_cli.profiles import profiles_to_serve

    config = load_config_readonly(config_path=source_home / "config.yaml") or {}
    gateway = GatewayConfig.from_dict(dict(config))
    if host_kind == "web":
        candidates = profiles_to_serve(multiplex=True)
    elif gateway.multiplex_profiles:
        candidates = profiles_to_serve(
            multiplex=True,
            profile_allowlist=gateway.multiplex_profile_allowlist,
        )
    else:
        profile = (
            source_home.name if source_home.parent.name == "profiles" else "default"
        )
        candidates = [(profile, source_home)]
    return _valid_profile_homes(candidates)


def _default_service(home: Path) -> AgentHandoffService:
    return AgentHandoffService(HandoffStore(home / "handoffs.db"))


@dataclass(slots=True)
class _ProfileRuntime:
    profile: str
    home: Path
    service: object
    phase_cursor: int = 0
    before: dict[str, str | None] = field(default_factory=dict)


class AgentHandoffSupervisor:
    """One bounded worker over the explicit profile homes served by its host."""

    def __init__(
        self,
        profile_homes: Iterable[tuple[str, Path | str]],
        *,
        owner: str,
        completion_queue,
        service_factory: Callable[[Path], object] = _default_service,
    ) -> None:
        self._owner = f"{owner}-handoff"[:255]
        self._queue = completion_queue
        self._profiles = tuple(
            _ProfileRuntime(profile, home, service_factory(home))
            for profile, home in _valid_profile_homes(profile_homes)
        )
        self._profile_cursor = 0
        self._health_lock = threading.Lock()
        self._health = BackgroundServiceHealth(state="starting", code="registered")

    def health(self) -> BackgroundServiceHealth:
        with self._health_lock:
            return self._health

    def _set_health(self, state: str, code: str) -> None:
        with self._health_lock:
            self._health = BackgroundServiceHealth(
                state=state,
                code=code,
                heartbeat_at=_utc_now(),
            )

    def _selected_profiles(self) -> tuple[_ProfileRuntime, ...]:
        count = min(len(self._profiles), _PROFILE_BATCH)
        selected = (
            tuple(
                self._profiles[(self._profile_cursor + offset) % len(self._profiles)]
                for offset in range(count)
            )
            if count
            else ()
        )
        if self._profiles:
            self._profile_cursor = (self._profile_cursor + count) % len(self._profiles)
        return selected

    @staticmethod
    def _due(snapshot, now: datetime) -> bool:
        return snapshot.next_advance_at is None or snapshot.next_advance_at <= now

    def _handoff_page(
        self, runtime: _ProfileRuntime, now: datetime
    ) -> tuple[object, ...]:
        store = runtime.service.store
        start = runtime.phase_cursor
        for offset in range(len(_ACTIVE_PHASES)):
            index = (start + offset) % len(_ACTIVE_PHASES)
            phase = _ACTIVE_PHASES[index]
            before = runtime.before.get(phase)
            page = store.list({"phase": phase}, limit=_SCAN_PAGE, before=before)
            if page:
                runtime.before[phase] = page[-1].handoff_id
                if len(page) < _SCAN_PAGE:
                    runtime.before[phase] = None
                    runtime.phase_cursor = (index + 1) % len(_ACTIVE_PHASES)
                else:
                    runtime.phase_cursor = index
                due = tuple(item for item in page if self._due(item, now))
                if due:
                    return due
            else:
                runtime.before[phase] = None
                runtime.phase_cursor = (index + 1) % len(_ACTIVE_PHASES)
        return ()

    def _advance(self, profiles: tuple[_ProfileRuntime, ...], now: datetime) -> int:
        pages = [list(self._handoff_page(runtime, now)) for runtime in profiles]
        advanced = failures = 0
        while advanced < _ADVANCE_BATCH and any(pages):
            for runtime, page in zip(profiles, pages):
                if not page or advanced >= _ADVANCE_BATCH:
                    continue
                snapshot = page.pop(0)
                try:
                    runtime.service.advance(
                        snapshot.handoff_id,
                        budget_seconds=_ADVANCE_BUDGET_SECONDS,
                    )
                except Exception:
                    failures += 1
                    logger.warning("Agent handoff advancement failed")
                advanced += 1
        return failures

    def _return_event(self, runtime: _ProfileRuntime, delivery, lease) -> dict | None:
        route = delivery.route
        if (
            not isinstance(route, Mapping)
            or route.get("kind") != "bot"
            or route.get("profile") != runtime.profile
        ):
            runtime.service.store.fail_delivery(
                lease, failure_code="return_route_invalid"
            )
            return None
        if route.get("hop_count", _MAX_AUTOMATIC_HOPS) >= _MAX_AUTOMATIC_HOPS:
            runtime.service.store.fail_delivery(
                lease, failure_code="handoff_hop_limit"
            )
            return None
        event = {
            "type": "handoff_return",
            "delivery_id": delivery.delivery_id,
            "handoff_id": delivery.handoff_id,
            "event_sequence": delivery.event_sequence,
            "profile": route["profile"],
            "session_id": route["session_id"],
            "tool_call_id": route["tool_call_id"],
            "hop_count": route["hop_count"],
            "delivery_claim": {
                "owner": lease.owner,
                "epoch": lease.epoch,
                "expires_at": lease.expires_at.isoformat(),
            },
        }
        if route.get("session_key") is not None:
            event["session_key"] = route["session_key"]
        return event

    def _publish(self, profiles: tuple[_ProfileRuntime, ...], now: datetime) -> int:
        due = [
            list(runtime.service.store.due_deliveries(now=now, limit=_DELIVERY_BATCH))
            for runtime in profiles
        ]
        handled = failures = 0
        while handled < _DELIVERY_BATCH and any(due):
            for runtime, deliveries in zip(profiles, due):
                if not deliveries or handled >= _DELIVERY_BATCH:
                    continue
                delivery = deliveries.pop(0)
                handled += 1
                lease = runtime.service.store.claim_delivery(
                    delivery.delivery_id,
                    self._owner,
                    now=now,
                    lease_seconds=_DELIVERY_LEASE_SECONDS,
                )
                if lease is None:
                    continue
                try:
                    event = self._return_event(runtime, delivery, lease)
                    if event is None:
                        continue
                    self._queue.put_nowait(event)
                except StaleAdvanceLease:
                    failures += 1
                except Exception:
                    failures += 1
                    logger.warning("Agent handoff return publication failed")
                    try:
                        runtime.service.store.release_delivery(
                            lease,
                            next_attempt_at=_utc_now()
                            + timedelta(seconds=_DELIVERY_RETRY_SECONDS),
                            failure_code="queue_publish_failed",
                        )
                    except StaleAdvanceLease:
                        pass
        return failures

    def tick(self) -> None:
        if not self._profiles:
            self._set_health("degraded", "no_profiles")
            return
        now = _utc_now()
        profiles = self._selected_profiles()
        failures = self._advance(profiles, now)
        failures += self._publish(profiles, now)
        self._set_health(
            "degraded" if failures else "healthy",
            "tick_failed" if failures else "ready",
        )

    def run(self, stop_event: threading.Event) -> None:
        try:
            while not stop_event.is_set():
                self.tick()
                stop_event.wait(_TICK_SECONDS)
        finally:
            for runtime in self._profiles:
                try:
                    runtime.service.store.close()
                except Exception:
                    logger.warning("Agent handoff store close failed")
            self._set_health("unhealthy", "stopped")


def create_agent_handoff_supervisor(
    context: BackgroundServiceContext, *, source_home: Path | str
) -> AgentHandoffSupervisor:
    from tools.process_registry import process_registry

    home = Path(source_home).expanduser().resolve(strict=False)
    return AgentHandoffSupervisor(
        _served_profile_homes(home, context.host_kind),
        owner=context.host_instance_id,
        completion_queue=process_registry.completion_queue,
    )


__all__ = ["AgentHandoffSupervisor", "create_agent_handoff_supervisor"]
