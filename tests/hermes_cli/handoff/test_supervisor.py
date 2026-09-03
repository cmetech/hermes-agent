from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import queue
import sqlite3
import threading
from types import SimpleNamespace

from hermes_cli.handoff.models import ChannelObservation, HandoffEndpoint, HandoffSpec
from hermes_cli.handoff.service import AgentHandoffService
from hermes_cli.handoff.store import HandoffStore
from hermes_cli.handoff.supervisor import (
    AgentHandoffSupervisor,
    _served_profile_homes,
)


UTC = timezone.utc


class _FakeStore:
    def __init__(self, handoffs=()):
        self.handoffs = list(handoffs)
        self.closed = False

    def list(self, query, *, limit, before):
        matching = [item for item in self.handoffs if item.phase == query["phase"]]
        start = 0
        if before is not None:
            start = next(
                (index + 1 for index, item in enumerate(matching) if item.handoff_id == before),
                len(matching),
            )
        return tuple(matching[start : start + limit])

    def due_deliveries(self, *, now, limit, host_kind=None):
        return ()

    def close(self):
        self.closed = True


class _FakeService:
    def __init__(self, profile, handoffs, calls):
        self.store = _FakeStore(handoffs)
        self.profile = profile
        self.calls = calls

    def advance(self, handoff_id, *, budget_seconds):
        self.calls.append((self.profile, handoff_id, budget_seconds))


def _snapshot(name: str):
    return SimpleNamespace(
        handoff_id=name,
        phase="active",
        next_advance_at=None,
    )


def _conversation_store(
    path, *, profile="default", host_kind="gateway", hop_count=0, text="done"
):
    store = HandoffStore(path)
    spec = HandoffSpec(
        mode="conversation",
        endpoint=HandoffEndpoint.parse("hermes://local/reviewer"),
        prompt="Please review this.",
        output_schema=None,
        deadline_at=None,
        attribution={"profile": profile},
        required_capabilities=frozenset(),
        return_route={
            "kind": "bot",
            "host_kind": host_kind,
            "profile": profile,
            "session_id": "session-1",
            "session_key": f"agent:{profile}:telegram:dm:42",
            "tool_call_id": "call-1",
            "delivery_policy": "wake",
            "hop_count": hop_count,
        },
    )
    snapshot = store.create_or_get(
        f"bot/{profile}/session-1", "call-1", spec, spec.fingerprint
    )
    snapshot = store.bind(
        snapshot.handoff_id,
        "local_bot_cli",
        {"profile": "reviewer", "mechanism": "local_bot_cli"},
        {},
        snapshot.state_version,
    )
    lease = store.claim_advance(
        snapshot.handoff_id,
        "test",
        now=datetime.now(UTC),
        lease_seconds=30,
    )
    assert lease is not None
    store.journal_attempt(lease, "submit")
    encoded = text.encode()
    store.commit_observation(
        lease,
        ChannelObservation(
            phase="succeeded",
            terminal_result={
                "text": text,
                "sha256": sha256(encoded).hexdigest(),
                "media_type": "text/plain",
                "size_bytes": len(encoded),
            },
        ),
    )
    return store, snapshot.handoff_id, store.attention(snapshot.handoff_id, limit=1)[0]


def test_tick_is_bounded_and_rotates_across_profiles_and_handoffs(tmp_path, monkeypatch):
    import hermes_cli.handoff.supervisor as supervisor_module

    monkeypatch.setattr(supervisor_module, "_ADVANCE_BATCH", 2)
    monkeypatch.setattr(supervisor_module, "_PROFILE_BATCH", 2)
    monkeypatch.setattr(supervisor_module, "_SCAN_PAGE", 1)
    calls = []
    homes = []
    services = {}
    for profile, names in (
        ("default", ("a-1", "a-2")),
        ("beta", ("b-1",)),
        ("gamma", ("c-1",)),
    ):
        home = tmp_path if profile == "default" else tmp_path / "profiles" / profile
        home.mkdir(parents=True, exist_ok=True)
        homes.append((profile, home))
        services[home.resolve()] = _FakeService(
            profile, [_snapshot(name) for name in names], calls
        )

    supervisor = AgentHandoffSupervisor(
        homes,
        owner="host-1",
        completion_queue=queue.Queue(),
        service_factory=lambda home: services[home.resolve()],
    )

    supervisor.tick()
    supervisor.tick()

    assert calls == [
        ("default", "a-1", 2.0),
        ("beta", "b-1", 2.0),
        ("gamma", "c-1", 2.0),
        ("default", "a-2", 2.0),
    ]
    assert len({handoff_id for _, handoff_id, _ in calls[:2]}) == 2


def test_only_canonical_intended_profile_homes_are_opened(tmp_path):
    root = tmp_path / "hermes"
    good = root / "profiles" / "good"
    wrong = root / "profiles" / "wrong"
    outside = tmp_path / "outside"
    for path in (root, good, wrong, outside):
        path.mkdir(parents=True, exist_ok=True)
    escaped = root / "profiles" / "escaped"
    escaped.symlink_to(outside, target_is_directory=True)
    seen = []

    def service_factory(home):
        seen.append(home.resolve())
        return _FakeService(home.name, (), [])

    AgentHandoffSupervisor(
        [
            ("default", root),
            ("good", good),
            ("mismatch", wrong),
            ("escaped", escaped),
            ("Not-Canonical", root / "profiles" / "Not-Canonical"),
        ],
        owner="host-1",
        completion_queue=queue.Queue(),
        service_factory=service_factory,
    )

    assert seen == [root.resolve(), good.resolve()]


def test_constructing_profile_services_does_not_resolve_peer_credentials(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "hermes_cli.peers.resolve_peer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("peer credentials must resolve only during an operation")
        ),
    )
    supervisor = AgentHandoffSupervisor(
        [("default", tmp_path)],
        owner="host-1",
        completion_queue=queue.Queue(),
    )
    stop = threading.Event()
    stop.set()

    supervisor.run(stop)

    assert supervisor.health().code == "stopped"


def test_gateway_profiles_follow_the_existing_multiplex_configuration(
    tmp_path, monkeypatch
):
    beta = tmp_path / "profiles" / "beta"
    beta.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(
        "hermes_cli.config.load_config_readonly",
        lambda **_kwargs: {
            "gateway": {
                "multiplex_profiles": True,
                "multiplex_profile_allowlist": ["beta"],
            }
        },
    )

    def profiles_to_serve(*, multiplex, profile_allowlist=None):
        calls.append((multiplex, profile_allowlist))
        return [("default", tmp_path), ("beta", beta)]

    monkeypatch.setattr("hermes_cli.profiles.profiles_to_serve", profiles_to_serve)

    assert _served_profile_homes(tmp_path, "gateway") == (
        ("default", tmp_path.resolve()),
        ("beta", beta.resolve()),
    )
    assert calls == [(True, ["beta"])]


def test_delivery_is_claimed_before_bounded_event_publication(tmp_path):
    secret = "private result must not enter the queue"
    store, handoff_id, delivery = _conversation_store(
        tmp_path / "handoffs.db", text=secret
    )
    events = queue.Queue()
    service = SimpleNamespace(store=store, advance=lambda *_args, **_kwargs: None)
    supervisor = AgentHandoffSupervisor(
        [("default", tmp_path)],
        owner="host-1",
        completion_queue=events,
        service_factory=lambda _home: service,
    )

    supervisor.tick()

    event = events.get_nowait()
    assert event == {
        "type": "handoff_return",
        "host_kind": "gateway",
        "delivery_id": delivery.delivery_id,
        "handoff_id": handoff_id,
        "event_sequence": delivery.event_sequence,
        "profile": "default",
        "session_id": "session-1",
        "session_key": "agent:default:telegram:dm:42",
        "tool_call_id": "call-1",
        "hop_count": 0,
        "delivery_claim": {
            "owner": event["delivery_claim"]["owner"],
            "epoch": 1,
            "expires_at": event["delivery_claim"]["expires_at"],
        },
    }
    assert event["delivery_claim"]["owner"].startswith("host-1-")
    assert store.claim_delivery(
        delivery.delivery_id,
        "other-consumer",
        now=datetime.now(UTC),
        lease_seconds=30,
    ) is None
    assert secret not in json.dumps(event)
    assert len(json.dumps(event).encode()) < 4096
    assert supervisor.health().message == ""
    store.close()


def test_only_the_initiating_host_claims_a_durable_return(tmp_path):
    store, _handoff_id, delivery = _conversation_store(
        tmp_path / "handoffs.db", host_kind="web"
    )
    service = SimpleNamespace(store=store, advance=lambda *_args, **_kwargs: None)
    gateway_events = queue.Queue()
    gateway = AgentHandoffSupervisor(
        [("default", tmp_path)],
        owner="gateway-host",
        host_kind="gateway",
        completion_queue=gateway_events,
        service_factory=lambda _home: service,
    )

    gateway.tick()

    assert gateway_events.empty()
    assert store.get_delivery(delivery.delivery_id).attempts == 0

    web_events = queue.Queue()
    web = AgentHandoffSupervisor(
        [("default", tmp_path)],
        owner="web-host",
        host_kind="web",
        completion_queue=web_events,
        service_factory=lambda _home: service,
    )
    web.tick()

    assert web_events.get_nowait()["delivery_id"] == delivery.delivery_id
    assert store.get_delivery(delivery.delivery_id).attempts == 1
    store.close()


def test_publish_failure_releases_and_restart_reclaims_expired_delivery(tmp_path):
    store, _handoff_id, delivery = _conversation_store(tmp_path / "handoffs.db")

    class BrokenQueue:
        def put_nowait(self, _event):
            raise RuntimeError("queue unavailable with token=secret")

    service = SimpleNamespace(store=store, advance=lambda *_args, **_kwargs: None)
    failed = AgentHandoffSupervisor(
        [("default", tmp_path)],
        owner="host-1",
        completion_queue=BrokenQueue(),
        service_factory=lambda _home: service,
    )
    failed.tick()
    released = store.get_delivery(delivery.delivery_id)
    assert released.state == "pending"
    assert released.failure_code == "queue_publish_failed"
    assert released.next_attempt_at is not None

    with store._lock:
        store._conn.execute(
            "UPDATE handoff_deliveries SET next_attempt_at=NULL WHERE delivery_id=?",
            (delivery.delivery_id,),
        )
    stale = store.claim_delivery(
        delivery.delivery_id,
        "dead-host",
        now=datetime.now(UTC) - timedelta(minutes=1),
        lease_seconds=1,
    )
    assert stale is not None
    events = queue.Queue()
    restarted = AgentHandoffSupervisor(
        [("default", tmp_path)],
        owner="host-2",
        completion_queue=events,
        service_factory=lambda _home: service,
    )
    restarted.tick()

    assert events.get_nowait()["delivery_id"] == delivery.delivery_id
    assert store.get_delivery(delivery.delivery_id).attempts == 3
    store.close()


def test_expired_handoff_lease_is_advanced_after_restart(tmp_path):
    store = HandoffStore(tmp_path / "handoffs.db")
    spec = HandoffSpec(
        mode="task",
        endpoint=HandoffEndpoint.parse("hermes://local/reviewer"),
        prompt="review",
        output_schema=None,
        deadline_at=None,
        attribution={},
        required_capabilities=frozenset(),
    )
    snapshot = store.create_or_get("workflow/run-1", "review", spec, spec.fingerprint)
    assert store.claim_advance(
        snapshot.handoff_id,
        "dead-host",
        now=datetime.now(UTC) - timedelta(minutes=1),
        lease_seconds=1,
    ) is not None

    class Channel:
        def bind(self, _snapshot, *, budget_seconds):
            return ChannelObservation(
                phase="prepared",
                mechanism="local_cli",
                binding={"profile": "reviewer", "mechanism": "local_cli"},
            )

        def cleanup_committed(self, _snapshot):
            return None

    service = AgentHandoffService(store, Channel())
    supervisor = AgentHandoffSupervisor(
        [("default", tmp_path)],
        owner="host-2",
        completion_queue=queue.Queue(),
        service_factory=lambda _home: service,
    )

    supervisor.tick()

    assert store.get(snapshot.handoff_id).mechanism == "local_cli"
    store.close()


def test_hop_one_delivery_remains_attention_without_another_wake(tmp_path):
    store, handoff_id, delivery = _conversation_store(
        tmp_path / "handoffs.db", hop_count=1
    )
    events = queue.Queue()
    supervisor = AgentHandoffSupervisor(
        [("default", tmp_path)],
        owner="host-1",
        completion_queue=events,
        service_factory=lambda _home: SimpleNamespace(
            store=store, advance=lambda *_args, **_kwargs: None
        ),
    )

    supervisor.tick()

    assert events.empty()
    assert store.get_delivery(delivery.delivery_id).failure_code == "handoff_hop_limit"
    assert store.has_attention(handoff_id) is True
    store.close()


def test_failed_delivery_claims_are_still_bounded_per_tick(tmp_path, monkeypatch):
    import hermes_cli.handoff.supervisor as supervisor_module

    monkeypatch.setattr(supervisor_module, "_DELIVERY_BATCH", 2)
    claims = []

    class Store(_FakeStore):
        def __init__(self, profile):
            super().__init__()
            self.deliveries = [
                SimpleNamespace(
                    delivery_id=f"{profile}-{index}",
                    handoff_id=f"handoff-{profile}-{index}",
                    event_sequence=index,
                    route={
                        "kind": "bot",
                        "host_kind": "gateway",
                        "profile": profile,
                        "session_id": "session-1",
                        "tool_call_id": "call-1",
                        "hop_count": 1,
                    },
                )
                for index in range(3)
            ]

        def due_deliveries(self, *, now, limit, host_kind=None):
            assert host_kind == "gateway"
            return tuple(self.deliveries[:limit])

        def claim_delivery(self, delivery_id, owner, *, now, lease_seconds):
            claims.append(delivery_id)
            return SimpleNamespace(
                owner=owner,
                epoch=1,
                expires_at=now + timedelta(seconds=lease_seconds),
            )

        def fail_delivery(self, _lease, *, failure_code):
            assert failure_code == "handoff_hop_limit"

    homes = [("default", tmp_path)]
    beta = tmp_path / "profiles" / "beta"
    beta.mkdir(parents=True)
    homes.append(("beta", beta))
    stores = {profile: Store(profile) for profile, _home in homes}
    supervisor = AgentHandoffSupervisor(
        homes,
        owner="host-1",
        completion_queue=queue.Queue(),
        service_factory=lambda home: SimpleNamespace(
            store=stores["default" if home == tmp_path else home.name],
            advance=lambda *_args, **_kwargs: None,
        ),
    )

    supervisor.tick()

    assert len(claims) == 2


def test_run_stops_cooperatively_closes_stores_and_health_is_cached(
    tmp_path, monkeypatch
):
    service = _FakeService("default", (), [])
    supervisor = AgentHandoffSupervisor(
        [("default", tmp_path)],
        owner="host-1",
        completion_queue=queue.Queue(),
        service_factory=lambda _home: service,
    )
    stop = threading.Event()
    stop.set()

    supervisor.run(stop)
    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cached health must not open SQLite")
        ),
    )

    assert service.store.closed is True
    assert supervisor.health().code == "stopped"


def test_automatic_return_wake_is_an_explicit_config_default():
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["bot_mode"]["handoff_return_wake"] is True
