"""Marketplace lifecycle through authenticated APIs and real temporary Git bytes."""

import json
import os
import threading

from plugins.workflow.store import RunStore

from test_marketplace_service import published_repo, service, _write_package  # noqa: F401
from test_marketplace_lifecycle_api import lifecycle_api, IDENTITY, V2  # noqa: F401


def test_real_workflow_backend_initialization_allows_marketplace_install(lifecycle_api):
    api = lifecycle_api
    # Desktop starts the normal workflow runtime before opening Marketplace.
    # Isolate the inherited process umask so this models a usual POSIX home.
    previous_umask = os.umask(0o022)
    try:
        RunStore(api.home)
    finally:
        os.umask(previous_umask)
    review = api.start("/install/prepare", {"identifier": "company/laptop-support"})
    assert review["state"] == "succeeded", (review["error"], review["outcome"])
    installed, _ = _confirm(api, review, "/install/confirm")
    assert installed["outcome"]["package_state"]["state"] == "installed"


def test_workflow_runtime_recovery_preserves_live_marketplace_preparation(
    lifecycle_api,
):
    api = lifecycle_api
    review = api.review()
    (envelope,) = api.service.transactions.staging_root.iterdir()
    metadata = envelope.stat()
    before = {
        path.relative_to(envelope): path.read_bytes()
        for path in envelope.rglob("*")
        if path.is_file()
    }
    # Reverse startup order proves this is an ownership collision as well as a
    # directory-mode collision: ordinary runtime recovery must preserve a live
    # package review owned by the marketplace transaction journal/marker.
    RunStore(api.home)
    RunStore(api.home)
    assert {
        path.relative_to(envelope): path.read_bytes()
        for path in envelope.rglob("*")
        if path.is_file()
    } == before
    after = envelope.stat()
    assert (after.st_dev, after.st_ino, after.st_mode) == (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
    )
    installed, _ = _confirm(api, review, "/install/confirm")
    assert installed["outcome"]["package_state"]["state"] == "installed"


def _confirm(api, review, path):
    request = api.confirm_request(review)
    response = api.post(path, request)
    assert response.status_code == 202, response.text
    operation = api.wait(response.json()["id"])
    assert operation["outcome"]["type"] == "committed", (
        operation["error"],
        operation["outcome"],
    )
    return operation, request


def _state(api):
    response = api.client.get(V2 + "/packages/company/laptop-support/state")
    assert response.status_code == 200, response.text
    return response.json()


def test_local_removal_review_with_concurrent_read_only_inspection(
    lifecycle_api, monkeypatch
):
    """A remote read must not contradict local verified removal availability."""
    api = lifecycle_api
    api.install()
    entered, release = threading.Event(), threading.Event()
    original = api.service.inspect

    def inspect(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(api.service, "inspect", inspect)
    response = api.post("/packages/company/laptop-support", api.request({}))
    assert response.status_code == 202, response.text
    assert entered.wait(5)
    try:
        assert _state(api)["state"] == "installed"
        assert _state(api)["busy"] is False
        removal = api.post("/remove/prepare", api.request({"identity": IDENTITY}))
        assert removal.status_code == 202, removal.text
        review = api.wait(removal.json()["id"])
        assert review["state"] == "succeeded"
        _confirm(api, review, "/remove/confirm")
        assert _state(api)["state"] == "absent"
    finally:
        release.set()
    assert api.wait(response.json()["id"])["state"] == "succeeded"
    assert _state(api)["state"] == "absent"


def test_exact_admission_replay_trust_one_all_changed_update_and_removal(lifecycle_api):
    api = lifecycle_api
    inspection = api.start("/packages/company/laptop-support", {})
    assert inspection["result"]["type"] == "package_detail"
    review = api.review()
    assert _state(api)["state"] == "absent"
    installed, request = _confirm(api, review, "/install/confirm")
    # The transport caller loses the first response; its exact request discovers
    # the original admission after real filesystem/provenance writes completed.
    replay = api.post("/install/confirm", request)
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == installed["id"]
    lookup = api.client.get(V2 + "/admissions/" + request["request_id"])
    assert lookup.json()["operation"]["id"] == installed["id"]
    first = _state(api)
    assert first["installed"]["version"] == "1.0.0"
    assert {
        item["workflow_name"]: item["state"] for item in first["trust"]["workflows"]
    } == {"diagnostic": "untrusted", "repair": "untrusted"}
    one = api.review(
        "/trust/review", {"identity": IDENTITY, "workflow_name": "diagnostic"}
    )
    granted, _ = _confirm(api, one, "/trust/grant")
    assert {
        item["workflow_name"]: item["state"]
        for item in granted["result"]["value"]["workflows"]
    } == {"diagnostic": "trusted", "repair": "untrusted"}
    all_review = api.review("/trust/review", {"identity": IDENTITY})
    _confirm(api, all_review, "/trust/grant")
    assert all(item["state"] == "trusted" for item in _state(api)["trust"]["workflows"])
    _write_package(api.repository.work, "laptop-support", version="2.0.0", marker="v2")
    api.repository.publish("publish changed distribution")
    updated, _ = _confirm(
        api, api.review("/update/prepare", {"identity": IDENTITY}), "/update/confirm"
    )
    current = _state(api)
    assert current["installed"]["version"] == "2.0.0"
    assert (
        current["trust"]["distribution_digest"] != first["trust"]["distribution_digest"]
    )
    assert all(item["state"] == "untrusted" for item in current["trust"]["workflows"])
    assert updated["outcome"]["package_state"]["installed"]["version"] == "2.0.0"
    _confirm(
        api, api.review("/remove/prepare", {"identity": IDENTITY}), "/remove/confirm"
    )
    assert _state(api)["state"] == "absent"
    # Retained successful installation is historical evidence, not current state.
    history = api.client.get(V2 + "/operations/" + installed["id"]).json()
    assert history["outcome"]["package_state"]["installed"]["version"] == "1.0.0"
    assert "confirmation_token" not in json.dumps(history)
