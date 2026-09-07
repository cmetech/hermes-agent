"""Authenticated lifecycle requests against real temporary repositories and homes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from test_marketplace_service import published_repo, service  # noqa: F401
from test_marketplace_api import _Authority, _verified_operator

from plugins.workflow.marketplace.api import (
    WorkflowMarketplaceApiContext,
    _actor,
    create_marketplace_router,
)
from plugins.workflow.marketplace.lifecycle_models import LifecycleOperation
from plugins.workflow.marketplace.models import WorkflowMarketplaceSource
from plugins.workflow.marketplace.service import WorkflowMarketplaceService


ROOT = "/api/plugins/workflow/marketplace"
V2 = ROOT + "/lifecycle/v2"
IDENTITY = {"source_key": "company", "package_id": "laptop-support"}
SUBJECT = {"type": "package", "identity": IDENTITY}
PRINCIPAL_HEADER = "X-Hermes-Marketplace-Principal-Binding"


# Break caught: capabilities omit binding or publish a private principal, or rotate
# binding when an idle profile registry is retired and reconstructed.
def test_principal_binding_survives_real_profile_retirement(
    lifecycle_api, tmp_path, caplog
):
    api = lifecycle_api
    first = api.client.get(V2 + "/capabilities").json()
    assert len(first["principal_binding"]) == 64
    origin = api.home
    api.home = tmp_path / "replacement"
    other = api.client.get(V2 + "/capabilities").json()
    assert other["principal_binding"] != first["principal_binding"]
    api.home = origin
    rebuilt = api.client.get(V2 + "/capabilities").json()
    assert api.context.current()[3] is not api.registry
    assert rebuilt["principal_binding"] == first["principal_binding"]
    assert rebuilt["registry_epoch"] == first["registry_epoch"]
    foreign = api.client.get(
        V2 + "/capabilities", headers={"X-Test-Actor": "private-other-user"}
    ).json()
    assert foreign["principal_binding"] != first["principal_binding"]
    exposed = json.dumps([first, other, rebuilt, foreign]) + caplog.text
    for private in (
        api.actor,
        "private-other-user",
        "session:provider:org:raw-user-identity",
    ):
        assert private not in exposed


# Break caught: malformed capability producer values bypass response revalidation.
@pytest.mark.parametrize(
    "mutation",
    ["missing", "extra", "uppercase", "short", "long", "number", "null", "newline"],
)
def test_principal_capabilities_faults_are_fixed_internal_errors(
    lifecycle_api, monkeypatch, mutation
):
    from plugins.workflow.marketplace import lifecycle_api as routes

    def malformed(**kwargs):
        kwargs["principal_binding"] = "a" * 64
        if mutation == "missing":
            del kwargs["principal_binding"]
        elif mutation == "extra":
            kwargs["private_actor"] = "private-must-not-publish"
        else:
            kwargs["principal_binding"] = {
                "uppercase": "A" * 64,
                "short": "a" * 63,
                "long": "a" * 65,
                "number": 123,
                "null": None,
                "newline": "a" * 64 + "\n",
            }[mutation]
        return kwargs

    monkeypatch.setattr(routes, "_Capabilities", malformed)
    response = lifecycle_api.client.get(V2 + "/capabilities")
    assert response.status_code == 500
    assert response.json() == {"detail": {"code": "marketplace_internal_error"}}


_PRINCIPAL_ROUTES = [
    ("GET", "/operations"),
    ("GET", "/operations/{operation_id}"),
    ("POST", "/operations/{operation_id}/cancel"),
    ("GET", "/admissions/{request_id}"),
    ("POST", "/operations/{operation_id}/review-token"),
    ("GET", "/packages/company/laptop-support/state"),
    *[
        ("POST", path)
        for path in (
            "/updates/check",
            "/install/prepare",
            "/install/confirm",
            "/update/prepare",
            "/update/confirm",
            "/remove/prepare",
            "/remove/confirm",
            "/trust/review",
            "/trust/grant",
            "/trust/revoke",
            "/sources/company/refresh",
            "/packages/company/laptop-support",
        )
    ],
]


# Break caught: any V2 route admits missing/ambiguous/stale bindings, compares
# duplicates, or reaches a producer before enforcing the shared precondition.
@pytest.mark.parametrize("method,path", _PRINCIPAL_ROUTES)
def test_all_v2_routes_reject_principal_header_before_producer(
    lifecycle_api, monkeypatch, method, path, caplog
):
    from plugins.workflow.marketplace import lifecycle_api as routes

    api = lifecycle_api
    operation = api.review()
    confirm = api.confirm_request(operation)
    token_body = {
        "review_digest": operation["result"]["value"]["review_digest"],
        "subject": operation["subject"],
        "selection": operation["selection"],
    }
    if path.endswith("/review-token"):
        payload = token_body
    elif path.endswith("/confirm") or path == "/trust/grant":
        payload = confirm
    elif path == "/install/prepare":
        payload = api.request({"identifier": "company/laptop-support"})
    elif path in {
        "/update/prepare",
        "/remove/prepare",
        "/trust/review",
        "/trust/revoke",
    }:
        payload = api.request({"identity": IDENTITY})
    else:
        payload = api.request({})
    binding = api.client.get(V2 + "/capabilities").json()["principal_binding"]
    path = path.format(operation_id=operation["id"], request_id=operation["request_id"])
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("producer")
        raise RuntimeError("private-producer-must-not-run")

    compare = routes.hmac.compare_digest
    comparisons = []

    def tracked_compare(left, right):
        comparisons.append((left, right))
        return compare(left, right)

    with monkeypatch.context() as guard:
        for name in (
            "list_snapshot",
            "get_lifecycle",
            "cancel_lifecycle",
            "lookup_admission",
            "review_token",
            "start",
            "intersects_active_mutation",
        ):
            guard.setattr(api.registry, name, forbidden)
        guard.setattr(routes, "read_package_state", forbidden)
        guard.setattr(routes.hmac, "compare_digest", tracked_compare)
        api.client.headers.pop(PRINCIPAL_HEADER, None)
        wrong = ("b" if binding[0] != "b" else "c") + binding[1:]
        variants = [
            [],
            [(PRINCIPAL_HEADER, "private-malformed")],
            [(PRINCIPAL_HEADER, "a" * 63)],
            [(PRINCIPAL_HEADER, "a" * 65)],
            [(PRINCIPAL_HEADER, binding.upper())],
            [(PRINCIPAL_HEADER, "a" * 4096)],
            [(PRINCIPAL_HEADER, wrong)],
            [(PRINCIPAL_HEADER, binding), (PRINCIPAL_HEADER.lower(), binding)],
            [(PRINCIPAL_HEADER, binding), (PRINCIPAL_HEADER.swapcase(), wrong)],
        ]
        failures = []
        for index, headers in enumerate(variants):
            calls.clear()
            comparisons.clear()
            response = api.client.request(
                method, V2 + path, headers=headers, json=payload
            )
            expected_comparisons = (
                [(wrong, binding)] if headers == [(PRINCIPAL_HEADER, wrong)] else []
            )
            if (
                response.status_code != 409
                or response.json()
                != {"detail": {"code": "marketplace_principal_changed"}}
                or calls
                or comparisons != expected_comparisons
            ):
                failures.append((
                    index,
                    response.status_code,
                    response.json(),
                    len(calls),
                    len(comparisons),
                ))
        assert failures == []
    assert binding not in caplog.text
    assert "private-malformed" not in caplog.text
    response = api.client.request(
        method, V2 + path, headers={PRINCIPAL_HEADER.swapcase(): binding}, json=payload
    )
    expected = (
        410
        if path in {"/update/confirm", "/remove/confirm", "/trust/grant"}
        else 202
        if method == "POST" and not path.startswith("/operations/")
        else 200
    )
    assert response.status_code == expected, response.text


# Break caught: a copied binding authorizes another actor or changes owner scope.
def test_principal_binding_does_not_replace_actor_authorization(lifecycle_api):
    api = lifecycle_api
    operation = api.review()
    own = api.client.get(V2 + "/capabilities").json()["principal_binding"]
    foreign_headers = {"X-Test-Actor": "private-other-user"}
    foreign = api.client.get(V2 + "/capabilities", headers=foreign_headers).json()[
        "principal_binding"
    ]
    assert foreign != own
    for suffix in ("", "/cancel"):
        method = api.client.get if not suffix else api.client.post
        response = method(
            V2 + "/operations/" + operation["id"] + suffix,
            headers={**foreign_headers, PRINCIPAL_HEADER: own},
        )
        assert response.status_code == 409
        assert response.json() == {"detail": {"code": "marketplace_principal_changed"}}
        response = method(
            V2 + "/operations/" + operation["id"] + suffix,
            headers={**foreign_headers, PRINCIPAL_HEADER: foreign},
        )
        assert response.status_code == 404
    response = api.client.get(
        V2 + "/operations", headers={**foreign_headers, PRINCIPAL_HEADER: foreign}
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
    response = api.client.get(
        V2 + "/operations/" + operation["id"],
        headers={"X-Test-Authority": "read", PRINCIPAL_HEADER: own},
    )
    assert response.status_code == 200
    assert response.json()["id"] == operation["id"]


# Break caught: capabilities derive/disclose a binding before authentication.
@pytest.mark.parametrize("status", [401, 403])
def test_principal_capabilities_auth_failure_discloses_no_binding(
    lifecycle_api, status
):
    from fastapi import HTTPException

    def denied(request, scope):
        raise HTTPException(status_code=status, detail={"code": "denied"})

    app = FastAPI()
    app.include_router(
        create_marketplace_router(denied, context=lifecycle_api.context),
        prefix="/api/plugins/workflow",
    )
    with TestClient(app) as client:
        response = client.get(V2 + "/capabilities")
    assert response.status_code == status
    assert response.json() == {"detail": {"code": "denied"}}


class LifecycleApi:
    def __init__(self, service, published_repo):
        self.service = service
        self.repository = published_repo
        self.home = service.home
        self.context = WorkflowMarketplaceApiContext(
            service_factory=lambda home, profile: (
                service
                if home == service.home
                else WorkflowMarketplaceService(home, profile=profile)
            ),
            home_resolver=lambda: self.home,
            profile_resolver=lambda home: service.profile,
            max_profiles=1,
            operation_limits={"max_workers": 2, "max_in_flight": 32},
        )
        service.add_source(
            WorkflowMarketplaceSource(
                name="company", repositoryUrl=published_repo.remote.as_uri()
            )
        )
        service.refresh_source("company")
        app = FastAPI()
        app.include_router(
            create_marketplace_router(_verified_operator, context=self.context),
            prefix="/api/plugins/workflow",
        )
        self.client = TestClient(app)
        self.client.headers["X-Test-Authority"] = "admin"
        key, _, _, self.registry = self.context.current()
        self.actor = _actor(_Authority(frozenset({"admin"})), key)
        self.client.headers[PRINCIPAL_HEADER] = self.client.get(
            V2 + "/capabilities"
        ).json()["principal_binding"]

    def request(self, body):
        return {"request_id": self.registry.admissions.new_request_id(), "body": body}

    def post(self, path, payload):
        return self.client.post(V2 + path, json=payload)

    def start(self, path, body):
        response = self.post(path, self.request(body))
        assert response.status_code == 202, response.text
        operation = LifecycleOperation.model_validate(response.json())
        return self.wait(operation.id)

    def wait(self, operation_id):
        with self.registry._lock:
            future = self.registry._records[operation_id].future
        future.result(timeout=10)
        response = self.client.get(V2 + "/operations/" + operation_id)
        assert response.status_code == 200, response.text
        assert response.json()["id"] == operation_id
        LifecycleOperation.model_validate(response.json())
        return response.json()

    def review(self, path="/install/prepare", body=None):
        operation = self.start(path, body or {"identifier": "company/laptop-support"})
        assert operation["state"] == "succeeded", operation
        assert "confirmation_token" not in json.dumps(operation)
        return operation

    def token(self, operation):
        response = self.post(
            "/operations/" + operation["id"] + "/review-token",
            {
                "review_digest": operation["result"]["value"]["review_digest"],
                "subject": operation["subject"],
                "selection": operation["selection"],
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    def confirm_request(self, operation):
        token = self.token(operation)
        return self.request({
            "confirmation_token": token["confirmation_token"],
            "prepare_operation_id": operation["id"],
            "review_digest": token["review_digest"],
            "subject": {
                "type": "package",
                "identity": operation["result"]["value"]["identity"],
            },
            "selection": operation["selection"],
        })

    def install(self):
        response = self.post("/install/confirm", self.confirm_request(self.review()))
        assert response.status_code == 202, response.text
        operation = self.wait(response.json()["id"])
        assert operation["outcome"]["type"] == "committed", operation
        return operation


@pytest.fixture
def lifecycle_api(service, published_repo):
    harness = LifecycleApi(service, published_repo)
    try:
        with harness.client:
            yield harness
    finally:
        harness.context.close()


def test_capabilities_are_authenticated_and_share_registry_epoch(lifecycle_api):
    api = lifecycle_api
    response = api.client.get(V2 + "/capabilities")
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["schema_version"] == 2
    assert value["profile"] == "support"
    assert value["registry_epoch"] == api.registry.admissions.epoch
    assert datetime.fromisoformat(value["server_time"].replace("Z", "+00:00"))
    for capability in (
        "admission_replay",
        "operations",
        "transactions",
        "trust",
        "package_state",
    ):
        assert capability in value["capabilities"]
    assert (
        api.client.get(
            V2 + "/capabilities", headers={"X-Test-Authority": "none"}
        ).status_code
        == 403
    )
    assert (
        api.post(
            "/install/prepare", api.request({"identifier": "company/laptop-support"})
        ).status_code
        == 202
    )


@pytest.mark.parametrize(
    "boundary", ["capabilities", "admission", "page", "token", "get", "cancel"]
)
def test_malformed_producer_never_publishes_success(
    lifecycle_api, monkeypatch, boundary
):
    from plugins.workflow.marketplace import lifecycle_api as routes
    from plugins.workflow.marketplace.operations import (
        AdmissionEvicted,
        LifecycleOperationPage,
        ReviewTokenResponse,
    )

    api = lifecycle_api
    operation = api.review()
    secret = "SECRET_PRODUCER_VALUE_do_not_publish"
    if boundary == "capabilities":
        original = routes._Capabilities
        monkeypatch.setattr(
            routes,
            "_Capabilities",
            lambda **kwargs: original.model_construct(**{
                **kwargs,
                "server_time": secret,
            }),
        )
        response = api.client.get(V2 + "/capabilities")
    elif boundary == "admission":
        malformed = AdmissionEvicted.model_construct(
            state="evicted",
            operation_id=operation["id"],
            request_id=operation["request_id"],
            registry_epoch=api.registry.admissions.epoch,
            profile="support",
            kind=secret,
            subject=operation["subject"],
            selection=None,
        )
        monkeypatch.setattr(
            api.registry, "lookup_admission", lambda *args, **kwargs: malformed
        )
        response = api.client.get(V2 + "/admissions/" + operation["request_id"])
    elif boundary == "page":
        malformed = LifecycleOperationPage.model_construct(
            items=(), next_cursor=secret, complete=True
        )
        monkeypatch.setattr(api.registry, "list_snapshot", lambda **kwargs: malformed)
        response = api.client.get(V2 + "/operations")
    elif boundary == "token":
        token = api.token(operation)
        malformed = ReviewTokenResponse.model_construct(**{
            **token,
            "confirmation_token": secret + "!",
        })
        monkeypatch.setattr(
            api.registry, "review_token", lambda *args, **kwargs: malformed
        )
        response = api.post(
            "/operations/" + operation["id"] + "/review-token",
            {
                "review_digest": token["review_digest"],
                "subject": operation["subject"],
                "selection": None,
            },
        )
    else:
        malformed = LifecycleOperation.model_validate(operation).model_copy(
            update={"profile": secret + "\n"}
        )
        method = "get_lifecycle" if boundary == "get" else "cancel_lifecycle"
        monkeypatch.setattr(api.registry, method, lambda *args, **kwargs: malformed)
        path = V2 + "/operations/" + operation["id"]
        response = (
            api.client.get(path)
            if boundary == "get"
            else api.client.post(path + "/cancel")
        )
    assert response.status_code == 500, response.text
    assert response.json() == {"detail": {"code": "marketplace_internal_error"}}
    assert secret not in response.text


@pytest.mark.parametrize(
    "method,path",
    [("list_snapshot", "/operations"), ("lookup_admission", "/admissions/request")],
)
def test_response_construction_validation_failure_is_safe_internal_error(
    lifecycle_api, monkeypatch, method, path
):
    from plugins.workflow.marketplace.operations import AdmissionEvicted

    def invalid_producer(*args, **kwargs):
        return AdmissionEvicted.model_validate({"private_target": "SECRET"})

    monkeypatch.setattr(lifecycle_api.registry, method, invalid_producer)
    response = lifecycle_api.client.get(V2 + path)
    assert response.status_code == 500
    assert response.json() == {"detail": {"code": "marketplace_internal_error"}}


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation_id", "wmop_" + "1" * 12 + "_" + "2" * 32),
        ("request_id", "wmreq_" + "e" * 32 + "_1788523200000_" + "f" * 32),
        ("subject", {"type": "all_packages"}),
        ("selection", {"type": "all"}),
        ("review_digest", "a" * 64),
        ("expires_at", "2026-09-04T12:05:00Z"),
    ],
)
def test_token_route_rejects_valid_but_uncorrelated_producer(
    lifecycle_api, monkeypatch, field, value
):
    from plugins.workflow.marketplace.operations import ReviewTokenResponse

    api = lifecycle_api
    operation = api.review()
    token = api.token(operation)
    altered = ReviewTokenResponse.model_validate({**token, field: value})
    monkeypatch.setattr(api.registry, "review_token", lambda *args, **kwargs: altered)
    response = api.post(
        "/operations/" + operation["id"] + "/review-token",
        {
            "review_digest": token["review_digest"],
            "subject": operation["subject"],
            "selection": None,
        },
    )
    assert response.status_code == 500
    assert response.json() == {"detail": {"code": "marketplace_internal_error"}}
    assert token["confirmation_token"] not in response.text


@pytest.mark.parametrize(
    "producer", ["registry", "service", "http_code", "http_nested"]
)
def test_unknown_producer_errors_publish_only_fixed_internal_failure(
    lifecycle_api, monkeypatch, producer
):
    from fastapi import HTTPException
    from plugins.workflow.marketplace.operations import (
        MarketplaceOperationRegistryError,
    )
    from plugins.workflow.marketplace.service import WorkflowMarketplaceError

    secret = "token_shaped_" + "x" * 40

    def fail(**kwargs):
        if producer == "registry":
            raise MarketplaceOperationRegistryError(secret, secret)
        if producer == "service":
            raise WorkflowMarketplaceError(secret, secret)
        if producer == "http_code":
            raise HTTPException(status_code=409, detail={"code": secret})
        raise HTTPException(
            status_code=409,
            detail={"code": "marketplace_request_conflict", "body": {"token": secret}},
        )

    monkeypatch.setattr(lifecycle_api.registry, "list_snapshot", fail)
    response = lifecycle_api.client.get(V2 + "/operations")
    assert response.status_code == 500
    assert response.json() == {"detail": {"code": "marketplace_internal_error"}}
    assert secret not in response.text


@pytest.mark.parametrize("exception_name", ["ValueError", "GitSourceError"])
def test_valid_request_unclassified_producer_failure_is_internal(
    lifecycle_api, monkeypatch, exception_name
):
    from hermes_cli.git_source import GitSourceError

    exception_type = ValueError if exception_name == "ValueError" else GitSourceError

    def fail(**kwargs):
        raise exception_type("private producer diagnostic")

    monkeypatch.setattr(lifecycle_api.registry, "list_snapshot", fail)
    response = lifecycle_api.client.get(V2 + "/operations")
    assert response.status_code == 500
    assert response.json() == {"detail": {"code": "marketplace_internal_error"}}


def test_request_and_auth_rejections_precede_producer_failures(
    lifecycle_api, monkeypatch
):
    api = lifecycle_api

    def fail(*args, **kwargs):
        raise RuntimeError("producer must not run for rejected input")

    monkeypatch.setattr(api.registry, "list_snapshot", fail)
    monkeypatch.setattr(api.registry, "start", fail)
    for query in ("limit=0", "limit=101", "limit=bad", "limit=1&limit=2", "unknown=1"):
        response = api.client.get(V2 + "/operations?" + query)
        assert response.status_code == 422, response.text
        assert response.json() == {"detail": {"code": "marketplace_request_invalid"}}
    for body in ({"unexpected": True}, {"identifier": "invalid-single-component"}):
        response = api.post("/install/prepare", api.request(body))
        assert response.status_code == 422, response.text
        assert response.json() == {"detail": {"code": "marketplace_request_invalid"}}
    assert (
        api.client.get(
            V2 + "/operations", headers={"X-Test-Authority": "none"}
        ).status_code
        == 403
    )
    assert (
        api.client.post(
            V2 + "/install/prepare",
            json=api.request({"identifier": "company/laptop-support"}),
            headers={"X-Test-Authority": "read"},
        ).status_code
        == 403
    )


def test_invalid_embedded_install_path_is_rejected_as_request_input(lifecycle_api):
    response = lifecycle_api.post(
        "/install/prepare",
        lifecycle_api.request({
            "identifier": "https://example.test/public.git/../escape"
        }),
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "marketplace_request_invalid"}}


@pytest.mark.parametrize("boundary", ["capabilities", "evicted"])
def test_outer_route_rejects_missing_required_discriminator(
    lifecycle_api, monkeypatch, boundary
):
    from plugins.workflow.marketplace import lifecycle_api as routes

    api = lifecycle_api
    if boundary == "capabilities":
        monkeypatch.setattr(
            routes,
            "_Capabilities",
            lambda **kwargs: {
                key: value for key, value in kwargs.items() if key != "schema_version"
            },
        )
        response = api.client.get(V2 + "/capabilities")
    else:
        operation = api.review()
        value = {
            key: operation[key]
            for key in [
                "request_id",
                "registry_epoch",
                "profile",
                "kind",
                "subject",
                "selection",
            ]
        }
        value["operation_id"] = operation["id"]
        monkeypatch.setattr(
            api.registry, "lookup_admission", lambda *args, **kwargs: value
        )
        response = api.client.get(V2 + "/admissions/" + operation["request_id"])
    assert response.status_code == 500
    assert response.json() == {"detail": {"code": "marketplace_internal_error"}}


@pytest.mark.parametrize(
    "repository",
    [
        "\ufeffowner/public",
        "https://example.test/public.git",
        "http://example.test/public.git",
        "file:///fixture-example/public.git",
        "owner/public",
    ],
)
def test_exact_get_cancel_preserve_python_valid_repository_identity(
    lifecycle_api, monkeypatch, repository
):
    api = lifecycle_api
    operation = api.review()
    operation["result"]["value"]["repository_url"] = repository
    value = LifecycleOperation.model_validate(operation)
    monkeypatch.setattr(api.registry, "get_lifecycle", lambda *args, **kwargs: value)
    monkeypatch.setattr(api.registry, "cancel_lifecycle", lambda *args, **kwargs: value)
    for method, suffix in [("get", ""), ("post", "/cancel")]:
        response = getattr(api.client, method)(
            V2 + "/operations/" + operation["id"] + suffix
        )
        assert response.status_code == 200, response.text
        assert response.json() == operation


def test_producer_cannot_publish_known_error_as_http_success(
    lifecycle_api, monkeypatch
):
    from fastapi import HTTPException

    def fail(**kwargs):
        raise HTTPException(
            status_code=200, detail={"code": "marketplace_request_conflict"}
        )

    monkeypatch.setattr(lifecycle_api.registry, "list_snapshot", fail)
    response = lifecycle_api.client.get(V2 + "/operations")
    assert response.status_code == 500
    assert response.json() == {"detail": {"code": "marketplace_internal_error"}}


def test_valid_real_responses_survive_exact_revalidation_and_keep_tokens_private(
    lifecycle_api,
):
    from plugins.workflow.marketplace.lifecycle_api import _Capabilities
    from plugins.workflow.marketplace.operations import (
        AdmissionFound,
        LifecycleOperationPage,
        ReviewTokenResponse,
    )

    api = lifecycle_api
    operation = api.review()
    token = api.token(operation)
    for path, model in [
        ("/capabilities", _Capabilities),
        ("/operations", LifecycleOperationPage),
        ("/admissions/" + operation["request_id"], AdmissionFound),
        ("/operations/" + operation["id"], LifecycleOperation),
    ]:
        response = api.client.get(V2 + path)
        assert response.status_code == 200, response.text
        assert (
            model.model_validate_json(response.content).model_dump(mode="json")
            == response.json()
        )
        assert token["confirmation_token"] not in response.text
    response = api.client.post(V2 + "/operations/" + operation["id"] + "/cancel")
    assert response.status_code == 200
    assert response.json() == operation
    assert (
        ReviewTokenResponse.model_validate_json(json.dumps(token)).model_dump(
            mode="json"
        )
        == token
    )


def test_replay_after_lost_confirm_response_admits_only_once(lifecycle_api):
    api = lifecycle_api
    review = api.review()
    request = api.confirm_request(review)
    # Admit via HTTP and deliberately discard the response body.
    assert api.post("/install/confirm", request).status_code == 202
    lookup = api.client.get(V2 + "/admissions/" + request["request_id"])
    assert lookup.status_code == 200, lookup.text
    first_id = lookup.json()["operation"]["id"]
    committed = api.wait(first_id)
    assert committed["outcome"]["type"] == "committed"
    replay = api.post("/install/confirm", request)
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == first_id
    assert (
        len([
            r
            for r in api.registry._records.values()
            if r.receipt.request_id == request["request_id"]
        ])
        == 1
    )
    assert request["body"][
        "confirmation_token"
    ] not in replay.text + lookup.text + json.dumps(committed)


def test_selected_grant_returns_complete_map(lifecycle_api):
    api = lifecycle_api
    api.install()
    review = api.review(
        "/trust/review", {"identity": IDENTITY, "workflow_name": "diagnostic"}
    )
    assert review["selection"] == {"type": "one", "workflow_name": "diagnostic"}
    assert len(review["result"]["value"]["package_workflows"]) == 2
    response = api.post("/trust/grant", api.confirm_request(review))
    assert response.status_code == 202, response.text
    operation = api.wait(response.json()["id"])
    assert operation["outcome"]["type"] == "committed", operation
    states = {
        item["workflow_name"]: item["state"]
        for item in operation["result"]["value"]["workflows"]
    }
    assert states == {"diagnostic": "trusted", "repair": "untrusted"}
    assert (
        operation["result"]["value"]["workflows"]
        == operation["outcome"]["package_state"]["trust"]["workflows"]
    )


@pytest.mark.parametrize(
    "change",
    [
        {"prepare_operation_id": "wmop_aaaaaaaaaaaa_" + "a" * 32},
        {
            "subject": {
                "type": "package",
                "identity": {"source_key": "other", "package_id": "laptop-support"},
            }
        },
        {"review_digest": "a" * 64},
        {"selection": {"type": "all"}},
        {"confirmation_token": "wrong-token-value-1234567890abcdef"},
    ],
)
def test_confirmation_must_match_exact_preparation(lifecycle_api, change):
    api = lifecycle_api
    request = api.confirm_request(api.review())
    request["body"].update(change)
    before = len(api.registry._records)
    response = api.post("/install/confirm", request)
    assert response.status_code in {410, 422}, response.text
    assert len(api.registry._records) == before
    assert not api.service.installed_packages()


def test_profile_capacity_keeps_service_receipts_and_review_authority(
    lifecycle_api, tmp_path
):
    api = lifecycle_api
    review = api.review()
    first = api.token(review)
    original_home = api.home
    api.home = tmp_path / "another-profile"
    refused = api.client.get(V2 + "/capabilities")
    assert refused.status_code == 429, refused.text
    api.home = original_home
    assert api.context.current()[2] is api.service
    assert api.token(review) == first
    assert (
        api.client.get(V2 + "/admissions/" + review["request_id"]).json()["operation"][
            "id"
        ]
        == review["id"]
    )


def test_direct_selector_matches_canonical_request_and_resolved_package(lifecycle_api):
    api = lifecycle_api
    review = api.review(
        body={
            "identifier": api.repository.remote.as_uri(),
            "ref": "main",
            "package_path": "packages/laptop-support",
        }
    )
    subject = review["subject"]
    assert subject["type"] == "direct_install"
    assert len(subject["selector_id"]) == 64
    assert subject["source_key"] == review["result"]["value"]["identity"]["source_key"]
    assert api.repository.remote.as_uri() not in json.dumps(subject)
    assert "packages/laptop-support" not in json.dumps(subject)
    response = api.post("/install/confirm", api.confirm_request(review))
    assert response.status_code == 202, response.text
    operation = api.wait(response.json()["id"])
    assert operation["subject"]["type"] == "package"
    assert operation["subject"]["identity"] == review["result"]["value"]["identity"]
    assert operation["outcome"]["type"] == "committed", operation


def test_stale_and_old_epoch_requests_never_admit(lifecycle_api):
    api = lifecycle_api
    for epoch, when, code in [
        (
            api.registry.admissions.epoch,
            datetime.now(timezone.utc) - timedelta(minutes=6),
            "marketplace_request_expired",
        ),
        ("0" * 32, datetime.now(timezone.utc), "marketplace_epoch_changed"),
    ]:
        request_id = f"wmreq_{epoch}_{int(when.timestamp() * 1000):013d}_{'a' * 32}"
        response = api.post("/updates/check", {"request_id": request_id, "body": {}})
        assert response.status_code == 409, response.text
        assert response.json() == {"detail": {"code": code}}
    assert not api.registry._records


def test_remaining_routes_use_actual_domain_calls(lifecycle_api):
    api = lifecycle_api
    refresh = api.start("/sources/company/refresh", {})
    assert refresh["outcome"]["type"] == "committed", refresh
    detail = api.start("/packages/company/laptop-support", {})
    assert detail["result"]["type"] == "package_detail", detail
    api.install()
    for body in ({}, {"identity": IDENTITY}):
        check = api.start("/updates/check", body)
        assert len(check["result"]["value"]["checks"]) == 1, check
    current = api.review("/update/prepare", {"identity": IDENTITY})
    assert current["result"]["value"]["confirmation_available"] is False
    assert current["result"]["value"]["expires_at"] is None
    review = api.review("/trust/review", {"identity": IDENTITY})
    confirmed = api.post("/trust/grant", api.confirm_request(review))
    assert confirmed.status_code == 202, confirmed.text
    assert api.wait(confirmed.json()["id"])["outcome"]["type"] == "committed"
    revoked = api.start(
        "/trust/revoke", {"identity": IDENTITY, "workflow_name": "diagnostic"}
    )
    assert revoked["outcome"]["type"] == "committed", revoked
    assert {
        item["workflow_name"]: item["state"]
        for item in revoked["result"]["value"]["workflows"]
    } == {"diagnostic": "untrusted", "repair": "trusted"}
    remove = api.review("/remove/prepare", {"identity": IDENTITY})
    response = api.post("/remove/confirm", api.confirm_request(remove))
    assert response.status_code == 202, response.text
    removed = api.wait(response.json()["id"])
    assert removed["outcome"]["package_state"]["state"] == "absent", removed
    assert (
        api.client.get(V2 + "/packages/company/laptop-support/state").json()["state"]
        == "absent"
    )


def test_invalid_bodies_and_read_only_authority_do_not_admit(lifecycle_api):
    api = lifecycle_api
    request = api.request({"identifier": "company/laptop-support"})
    for content in (
        json.dumps(request).replace('"body":', '"body": {}, "body":'),
        json.dumps({**request, "unexpected": True}),
        json.dumps({
            **request,
            "body": {
                "identifier": "company/laptop-support",
                "packagePath": "packages/laptop-support",
            },
        }),
        json.dumps({
            **request,
            "body": {"identifier": "company/laptop-support", "ref": False},
        }),
        '{"request_id":NaN,"body":{}}',
    ):
        response = api.client.post(
            V2 + "/install/prepare",
            content=content,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422, response.text
        assert response.json() == {"detail": {"code": "marketplace_request_invalid"}}
    response = api.client.post(
        V2 + "/install/prepare", json=request, headers={"X-Test-Authority": "read"}
    )
    assert response.status_code == 403
    assert not api.registry._records


def test_foreign_actor_cannot_observe_token_admission_or_cancel(lifecycle_api):
    api = lifecycle_api
    review = api.review()
    request = api.confirm_request(review)
    foreign = {"X-Test-Actor": "other-actor"}
    foreign[PRINCIPAL_HEADER] = api.client.get(
        V2 + "/capabilities", headers=foreign
    ).json()["principal_binding"]
    for suffix in (
        "/operations/" + review["id"],
        "/admissions/" + review["request_id"],
    ):
        response = api.client.get(V2 + suffix, headers=foreign)
        assert response.status_code == 404, response.text
    assert api.client.get(V2 + "/operations", headers=foreign).json()["items"] == []
    response = api.client.post(V2 + "/install/confirm", json=request, headers=foreign)
    assert response.status_code == 410, response.text
    response = api.client.post(
        V2 + "/operations/" + review["id"] + "/cancel", headers=foreign
    )
    assert response.status_code == 404
    assert len(api.registry._records) == 1


def test_expiry_and_consumption_never_reissue_tokens(lifecycle_api):
    api = lifecycle_api
    review = api.review()
    request = api.confirm_request(review)
    expiry = datetime.fromisoformat(
        review["result"]["value"]["expires_at"].replace("Z", "+00:00")
    )
    api.service.transactions.clock = lambda: expiry + timedelta(seconds=1)
    before = len(api.registry._records)
    assert api.post("/install/confirm", request).status_code == 410
    assert len(api.registry._records) == before
    assert (
        api.post(
            "/operations/" + review["id"] + "/review-token",
            {
                "subject": review["subject"],
                "selection": None,
                "review_digest": review["result"]["value"]["review_digest"],
            },
        ).status_code
        == 410
    )


def test_concurrent_confirm_replays_share_exact_http_admission(lifecycle_api):
    api = lifecycle_api
    request = api.confirm_request(api.review())
    with ThreadPoolExecutor(max_workers=4) as callers:
        responses = list(
            callers.map(lambda _: api.post("/install/confirm", request), range(8))
        )
    assert all(response.status_code == 202 for response in responses), [
        r.text for r in responses
    ]
    ids = {response.json()["id"] for response in responses}
    assert len(ids) == 1
    assert api.wait(ids.pop())["outcome"]["type"] == "committed"
    changed = {
        **request,
        "body": {
            **request["body"],
            "confirmation_token": "another-token-value-1234567890abcd",
        },
    }
    conflict = api.post("/install/confirm", changed)
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": {"code": "marketplace_request_conflict"}}
    assert api.post("/install/confirm", api.request(request["body"])).status_code == 410


def test_prepare_replay_survives_removed_source_and_result_eviction(lifecycle_api):
    api = lifecycle_api
    request = api.request({"identifier": "company/laptop-support"})
    response = api.post("/install/prepare", request)
    assert response.status_code == 202
    operation = api.wait(response.json()["id"])
    assert operation["state"] == "succeeded"
    api.service.remove_source("company")
    replay = api.post("/install/prepare", request)
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == operation["id"]
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    api.registry.clock = lambda: later
    api.registry.admissions.clock = lambda: later
    lookup = api.client.get(V2 + "/admissions/" + request["request_id"])
    assert lookup.status_code == 200, lookup.text
    assert lookup.json()["state"] == "evicted"
    replay = api.post("/install/prepare", request)
    assert replay.status_code == 202, replay.text
    assert replay.json()["state"] == "evicted"
    assert replay.json()["operation_id"] == operation["id"]
    assert not api.registry._records


@pytest.mark.parametrize(
    "fault_point,expected",
    [
        (None, "committed"),
        ("after_candidate_swap", "known_unchanged"),
        ("after_trust_revoke", "recovery_required"),
    ],
)
def test_update_http_outcome_matches_actual_candidate_and_recovery(
    lifecycle_api, monkeypatch, fault_point, expected
):
    from test_marketplace_service import _write_package
    from plugins.workflow.marketplace.models import InstalledPackageIdentity
    from plugins.workflow.marketplace.package import load_distribution

    api = lifecycle_api
    api.install()
    _write_package(api.repository.work, "laptop-support", version="2.0.0", marker="v2")
    api.repository.publish("update")
    review = api.review("/update/prepare", {"identity": IDENTITY})
    request = api.confirm_request(review)
    original = api.service.transactions.atomic_install

    def fault(point):
        if point == fault_point:
            raise OSError("private-candidate-cleanup")

    monkeypatch.setattr(
        api.service.transactions,
        "atomic_install",
        lambda *args, **kwargs: original(*args, **kwargs, fault=fault),
    )
    response = api.post("/update/confirm", request)
    assert response.status_code == 202, response.text
    operation = api.wait(response.json()["id"])
    assert operation["outcome"]["type"] == expected, operation
    identity = InstalledPackageIdentity(sourceKey="company", packageId="laptop-support")
    version = load_distribution(
        api.service.installed_store.package_root(identity)
    ).manifest.version
    assert version == ("1.0.0" if expected == "known_unchanged" else "2.0.0")
    state = api.client.get(V2 + "/packages/company/laptop-support/state").json()
    if expected == "recovery_required":
        assert operation["outcome"]["reason"] == "rollback_failed"
        assert state["state"] == "unconfirmed"
        assert state["installed"] is None and state["trust"] is None
    elif expected == "known_unchanged":
        assert operation["outcome"]["evidence"] == "rollback_verified"
        assert state["installed"]["version"] == "1.0.0"
    else:
        assert state["installed"]["version"] == "2.0.0"
    assert "private-candidate-cleanup" not in json.dumps(operation)


def test_direct_mismatched_issued_review_cannot_be_vaulted(lifecycle_api, monkeypatch):
    from plugins.workflow.marketplace.models import InstallRequest

    api = lifecycle_api
    original = api.service.prepare_install
    # Both requests resolve the same source/package but bind different exact refs.
    other = original(
        InstallRequest(
            identifier=api.repository.remote.as_uri(),
            ref="main",
            packagePath="packages/laptop-support",
        ),
        actor=api.actor,
    )
    monkeypatch.setattr(api.service, "prepare_install", lambda *args, **kwargs: other)
    operation = api.start(
        "/install/prepare",
        {
            "identifier": api.repository.remote.as_uri(),
            "package_path": "packages/laptop-support",
        },
    )
    assert operation["state"] == "failed", operation
    assert operation["result"] is None
    assert operation["id"] not in api.registry._review_tokens
    assert api.service.confirmation_metadata(
        other.confirmation_token, actor=api.actor, operation="install"
    )


def test_state_reports_foreign_active_mutation_without_foreign_visibility(
    lifecycle_api, monkeypatch
):
    api = lifecycle_api
    request = api.confirm_request(api.review())
    entered, release = threading.Event(), threading.Event()
    original = api.service.confirm_install

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(api.service, "confirm_install", blocked)
    response = api.post("/install/confirm", request)
    assert response.status_code == 202
    assert entered.wait(5)
    try:
        other = {"X-Test-Actor": "other-actor"}
        other[PRINCIPAL_HEADER] = api.client.get(
            V2 + "/capabilities", headers=other
        ).json()["principal_binding"]
        state = api.client.get(
            V2 + "/packages/company/laptop-support/state", headers=other
        )
        assert state.status_code == 200, state.text
        assert state.json()["busy"] is True
        assert state.json()["state"] == "absent"
        for path, body in [
            ("/packages/company/laptop-support", {}),
            ("/remove/prepare", {"identity": IDENTITY}),
        ]:
            rejected = api.post(path, api.request(body))
            assert rejected.status_code == 409
            assert rejected.json() == {
                "detail": {"code": "marketplace_operation_conflict"}
            }
        # Exact confirmation replay wins over the occupied lifecycle target.
        assert (
            api.post("/install/confirm", request).json()["id"] == response.json()["id"]
        )
        assert api.client.get(V2 + "/operations", headers=other).json()["items"] == []
        assert (
            api.client.get(
                V2 + "/operations/" + response.json()["id"], headers=other
            ).status_code
            == 404
        )
        unrelated = api.client.get(
            V2 + "/packages/company/neighbor/state", headers=other
        )
        assert unrelated.json()["busy"] is False
    finally:
        release.set()
    assert api.wait(response.json()["id"])["outcome"]["type"] == "committed"


@pytest.mark.parametrize("legacy", [False, True])
def test_cancel_http_before_mutation_returns_requested_v2_operation(
    lifecycle_api, monkeypatch, legacy
):
    api = lifecycle_api
    entered, release = threading.Event(), threading.Event()
    original = api.service.check_updates

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(api.service, "check_updates", blocked)
    response = (
        api.client.post(ROOT + "/updates/check", json={})
        if legacy
        else api.post("/updates/check", api.request({}))
    )
    assert response.status_code == 202
    operation_id = response.json()["id"]
    assert entered.wait(5)
    try:
        cancelled = api.post("/operations/" + operation_id + "/cancel", {})
        assert cancelled.status_code == 200, cancelled.text
        parsed = LifecycleOperation.model_validate(cancelled.json())
        assert parsed.id == operation_id
    finally:
        release.set()
    terminal = api.wait(operation_id)
    assert terminal["state"] == "cancelled", terminal
    assert terminal["outcome"]["type"] == "cancelled_before_commit"


def test_review_callback_allows_concurrent_http_observation_and_cancellation(
    lifecycle_api, monkeypatch
):
    api = lifecycle_api
    entered, release = threading.Event(), threading.Event()
    original = api.service.transactions.inspect_token
    calls = 0

    def blocked(*args, **kwargs):
        nonlocal calls
        calls += 1
        # complete_read metadata first inspects once; registry checks unused next.
        if calls == 2:
            entered.set()
            assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(api.service.transactions, "inspect_token", blocked)
    response = api.post(
        "/install/prepare", api.request({"identifier": "company/laptop-support"})
    )
    assert response.status_code == 202
    operation_id = response.json()["id"]
    assert entered.wait(5)
    try:
        with ThreadPoolExecutor(max_workers=2) as observers:
            observation = observers.submit(
                api.client.get, V2 + "/operations/" + operation_id
            )
            assert observation.result(timeout=3).json()["state"] == "running"
            cancellation = observers.submit(
                api.post, "/operations/" + operation_id + "/cancel", {}
            )
            assert cancellation.result(timeout=3).status_code == 200
    finally:
        release.set()
    terminal = api.wait(operation_id)
    assert terminal["state"] == "cancelled", terminal
    assert operation_id not in api.registry._review_tokens


def test_actual_context_recreation_fails_closed_but_legacy_token_remains_valid(
    lifecycle_api,
):
    api = lifecycle_api
    review = api.review()
    request = api.confirm_request(review)
    recreated = WorkflowMarketplaceApiContext(
        home_resolver=lambda: api.home, profile_resolver=lambda home: "support"
    )
    app = FastAPI()
    app.include_router(
        create_marketplace_router(_verified_operator, context=recreated),
        prefix="/api/plugins/workflow",
    )
    try:
        with TestClient(app, headers={"X-Test-Authority": "admin"}) as client:
            client.headers[PRINCIPAL_HEADER] = client.get(V2 + "/capabilities").json()[
                "principal_binding"
            ]
            assert (
                client.get(V2 + "/capabilities").json()["registry_epoch"]
                == api.registry.admissions.epoch
            )
            assert client.post(V2 + "/install/confirm", json=request).status_code == 410
            assert (
                client.get(V2 + "/admissions/" + review["request_id"]).status_code
                == 404
            )
            _, _, fresh_service, fresh_registry = recreated.current()
            assert fresh_service is not api.service
            assert not fresh_registry._records
            assert not fresh_service._issued_reviews
            installed = fresh_service.confirm_install(
                request["body"]["confirmation_token"], actor=api.actor
            )
            assert installed.version == "1.0.0"
            assert not fresh_service._issued_reviews
    finally:
        recreated.close()


def test_complete_http_snapshot_is_stable_private_and_capacity_bounded(lifecycle_api):
    api = lifecycle_api
    own = set()
    for index in range(106):
        if index % 3 == 0:
            response = api.client.post(ROOT + "/updates/check", json={})
            assert response.status_code == 202
            operation_id = response.json()["id"]
            api.registry._records[operation_id].future.result(timeout=10)
        else:
            operation_id = api.start("/updates/check", {})["id"]
        own.add(operation_id)
    foreign_headers = {"X-Test-Actor": "foreign"}
    foreign_headers[PRINCIPAL_HEADER] = api.client.get(
        V2 + "/capabilities", headers=foreign_headers
    ).json()["principal_binding"]
    foreign = api.client.post(
        V2 + "/updates/check", json=api.request({}), headers=foreign_headers
    )
    assert foreign.status_code == 202
    api.registry._records[foreign.json()["id"]].future.result(timeout=10)
    first = api.client.get(V2 + "/operations?limit=100")
    assert first.status_code == 200, first.text
    page = first.json()
    assert len(page["items"]) == 100 and page["complete"] is False
    cursor = page["next_cursor"]
    assert (
        api.client.get(
            V2 + "/operations",
            params={"cursor": cursor},
            headers=foreign_headers,
        ).status_code
        == 410
    )
    new = api.start("/updates/check", {})
    second = api.client.get(V2 + "/operations", params={"cursor": cursor})
    assert second.status_code == 200, second.text
    all_items = page["items"] + second.json()["items"]
    assert len(all_items) == len({item["id"] for item in all_items}) == 106
    assert {item["id"] for item in all_items} == own
    assert new["id"] not in own
    assert second.json()["complete"] is True
    for item in all_items:
        LifecycleOperation.model_validate(item)
    assert "confirmation_token" not in first.text + second.text
    for _ in range(3):
        assert api.client.get(V2 + "/operations").status_code == 200
    assert api.client.get(V2 + "/operations").status_code == 503
    later = datetime.now(timezone.utc) + timedelta(seconds=31)
    api.registry.clock = lambda: later
    assert (
        api.client.get(V2 + "/operations", params={"cursor": cursor}).status_code == 410
    )


def test_same_display_profile_does_not_share_receipts_or_tokens(
    lifecycle_api, tmp_path
):
    api = lifecycle_api
    review = api.review()
    request = api.confirm_request(review)
    api.context._max_profiles = 2
    original_home = api.home
    api.home = tmp_path / "second-real-profile"
    capabilities = api.client.get(V2 + "/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["profile"] == "support"
    assert capabilities.json()["registry_epoch"] == api.registry.admissions.epoch
    assert api.context.current()[2] is not api.service
    api.client.headers[PRINCIPAL_HEADER] = capabilities.json()["principal_binding"]
    assert api.client.get(V2 + "/admissions/" + review["request_id"]).status_code == 404
    assert api.post("/install/confirm", request).status_code == 410
    assert api.client.get(V2 + "/operations").json()["items"] == []
    api.home = original_home
    api.client.headers[PRINCIPAL_HEADER] = api.client.get(V2 + "/capabilities").json()[
        "principal_binding"
    ]
    assert (
        api.token(review)["confirmation_token"] == request["body"]["confirmation_token"]
    )


def test_concurrent_token_reads_retain_context_selection_and_immutable_issuance(
    lifecycle_api, monkeypatch, tmp_path
):
    from plugins.workflow.marketplace.lifecycle_state import review_token_metadata
    from plugins.workflow.marketplace.lifecycle_models import (
        PackageSubject,
        OneTrustSelection,
    )

    api = lifecycle_api
    api.install()
    issued = []
    original = api.service._remember_issued_review

    def capture(review):
        original(review)
        issued.append(review)

    monkeypatch.setattr(api.service, "_remember_issued_review", capture)
    reviews = [
        api.review(
            "/trust/review", {"identity": IDENTITY, "workflow_name": "diagnostic"}
        ),
        api.review("/remove/prepare", {"identity": IDENTITY}),
    ]
    with ThreadPoolExecutor(max_workers=2) as callers:
        tokens = list(callers.map(api.token, reviews))
    genuine = next(review for review in issued if hasattr(review, "workflows"))
    workflow = genuine.workflows[0]
    api.service.trust_store.trust(
        workflow.package_digest, actor="manual", risk_digest=workflow.risk_digest
    )
    authority = review_token_metadata(
        api.service,
        genuine,
        actor=api.actor,
        subject=PackageSubject.model_validate(SUBJECT),
        selection=OneTrustSelection(type="one", workflow_name="diagnostic"),
    )
    assert authority.validate_unused(genuine.confirmation_token)
    assert reviews[0]["result"]["value"]["workflows"][0]["trust_state"] == "untrusted"
    origin = api.home
    api.home = tmp_path / "unallocated"
    assert api.client.get(V2 + "/capabilities").status_code == 429
    api.home = origin
    assert [api.token(review) for review in reviews] == tokens
    assert api.context.current()[2] is api.service
    assert all(
        token["confirmation_token"] not in str(api.service._issued_reviews)
        for token in tokens
    )
    assert all(
        token["confirmation_token"]
        not in str(api.service._trust_confirmations._selections)
        for token in tokens
    )


def test_retired_context_can_be_recreated_only_after_receipt_expiry(
    lifecycle_api, tmp_path
):
    api = lifecycle_api
    operation = api.start("/updates/check", {})
    origin = api.home
    api.home = tmp_path / "replacement-profile"
    assert api.client.get(V2 + "/capabilities").status_code == 429
    future = datetime.now(timezone.utc) + timedelta(hours=25)
    api.registry.clock = lambda: future
    api.registry.admissions.clock = lambda: future
    assert api.client.get(V2 + "/capabilities").status_code == 200
    assert len(api.context._profiles) == 1
    api.home = origin
    # The replacement has no live work and is eligible for retirement.
    response = api.client.get(V2 + "/capabilities")
    assert response.status_code == 200
    assert response.json()["registry_epoch"] == operation["registry_epoch"]
    assert api.context.current()[3] is not api.registry


@pytest.mark.parametrize("failure", [False, True])
def test_late_http_cancel_cannot_erase_source_publication(
    lifecycle_api, monkeypatch, failure
):
    api = lifecycle_api
    entered, release = threading.Event(), threading.Event()
    original = api.service.refresh_source

    def published(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(10)
        if failure:
            raise OSError("private-post-publication-error")
        return result

    monkeypatch.setattr(api.service, "refresh_source", published)
    response = api.post("/sources/company/refresh", api.request({}))
    assert response.status_code == 202
    assert entered.wait(5)
    operation_id = response.json()["id"]
    try:
        assert (
            api.post("/operations/" + operation_id + "/cancel", {}).status_code == 200
        )
    finally:
        release.set()
    operation = api.wait(operation_id)
    assert operation["outcome"]["type"] == (
        "outcome_unknown" if failure else "committed"
    ), operation
    assert api.service.catalog.source_store.status("company").state == "fresh"
    assert "private-post-publication-error" not in json.dumps(operation)


@pytest.mark.parametrize(
    "fault_point,expected",
    [
        ("after_candidate_swap", "known_unchanged"),
        ("after_trust_revoke", "recovery_required"),
        (None, "committed"),
    ],
)
def test_late_http_cancel_respects_atomic_outcome(
    lifecycle_api, monkeypatch, fault_point, expected
):
    api = lifecycle_api
    request = api.confirm_request(api.review())
    entered, release = threading.Event(), threading.Event()
    original = api.service.transactions.atomic_install

    def fault(point):
        if point == (fault_point or "after_trust_revoke"):
            entered.set()
            assert release.wait(10)
            if fault_point:
                raise OSError("private-atomic-fault")

    monkeypatch.setattr(
        api.service.transactions,
        "atomic_install",
        lambda *args, **kwargs: original(*args, **kwargs, fault=fault),
    )
    response = api.post("/install/confirm", request)
    assert response.status_code == 202, response.text
    operation_id = response.json()["id"]
    assert entered.wait(5)
    try:
        with ThreadPoolExecutor(max_workers=1) as callers:
            cancelled = callers.submit(
                api.post, "/operations/" + operation_id + "/cancel", {}
            )
            assert cancelled.result(timeout=3).status_code == 200
    finally:
        release.set()
    operation = api.wait(operation_id)
    assert operation["outcome"]["type"] == expected, operation
    assert operation["state"] != "cancelled"
    if expected == "known_unchanged":
        assert operation["outcome"]["evidence"] == "rollback_verified"


def test_invalid_eligible_record_fails_both_lists_instead_of_disappearing(
    lifecycle_api,
):
    api = lifecycle_api
    response = api.client.post(ROOT + "/updates/check", json={})
    assert response.status_code == 202
    operation_id = response.json()["id"]
    api.registry._records[operation_id].future.result(timeout=10)
    with api.registry._lock:
        api.registry._records[operation_id].finished_at = None
    with TestClient(
        api.client.app,
        raise_server_exceptions=False,
        headers=dict(api.client.headers),
    ) as client:
        for path in (ROOT + "/operations", V2 + "/operations"):
            result = client.get(path)
            assert result.status_code >= 400, result.text
            assert '"items":[]' not in result.text


@pytest.mark.parametrize("operation_id", ["invalid", "wmop_aaaaaaaaaaaa_" + "a" * 32])
@pytest.mark.parametrize("version", [ROOT, V2])
def test_missing_and_malformed_http_ids_do_not_disclose_versions(
    lifecycle_api, operation_id, version
):
    api = lifecycle_api
    for method, suffix in [("get", ""), ("post", "/cancel")]:
        response = getattr(api.client, method)(
            version + "/operations/" + operation_id + suffix
        )
        assert response.status_code == 404
        assert response.json() == {
            "detail": {"code": "marketplace_operation_not_found"}
        }
    assert (
        api.client.post(
            version + "/operations/" + operation_id + "/cancel",
            headers={"X-Test-Authority": "read"},
        ).status_code
        == 403
    )


@pytest.mark.parametrize("mismatch", ["identity", "profile"])
def test_local_state_must_match_requested_scope(lifecycle_api, monkeypatch, mismatch):
    from plugins.workflow.marketplace import lifecycle_api as routes
    from plugins.workflow.marketplace.models import InstalledPackageIdentity

    api = lifecycle_api
    state = routes.read_package_state(
        api.service, InstalledPackageIdentity(sourceKey="company", packageId="neighbor")
    )
    if mismatch == "profile":
        state = state.model_copy(
            update={
                "profile": "different-profile",
                "identity": state.identity.model_copy(
                    update={"package_id": "laptop-support"}
                ),
            }
        )
    monkeypatch.setattr(routes, "read_package_state", lambda *args: state)
    response = api.client.get(V2 + "/packages/company/laptop-support/state")
    assert response.status_code >= 400, response.text


def test_confirm_replay_remains_available_at_receipt_capacity_and_eviction(
    lifecycle_api, monkeypatch
):
    from plugins.workflow.marketplace import admissions

    api = lifecycle_api
    monkeypatch.setattr(admissions, "_RECEIPTS_MAX", 2)
    request = api.confirm_request(api.review())
    response = api.post("/install/confirm", request)
    assert response.status_code == 202
    operation_id = response.json()["id"]
    assert api.wait(operation_id)["outcome"]["type"] == "committed"
    rejected = api.post("/updates/check", api.request({}))
    assert rejected.status_code == 503, rejected.text
    assert rejected.json() == {"detail": {"code": "marketplace_admission_capacity"}}
    assert api.post("/install/confirm", request).json()["id"] == operation_id
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    api.registry.clock = lambda: later
    api.registry.admissions.clock = lambda: later
    evicted = api.post("/install/confirm", request)
    assert evicted.status_code == 202, evicted.text
    assert evicted.json()["state"] == "evicted"
    assert evicted.json()["operation_id"] == operation_id
    assert "confirmation_token" not in evicted.text


def test_ambiguous_recovery_error_after_real_install_keeps_candidate_truth(
    lifecycle_api, monkeypatch
):
    from plugins.workflow.marketplace.package import WorkflowMarketplaceError

    api = lifecycle_api
    request = api.confirm_request(api.review())
    original = api.service.confirm_install

    def ambiguous(*args, **kwargs):
        installed = original(*args, **kwargs)
        assert installed.version == "1.0.0"
        # Inject a corrupt recovery record after actual candidate publication.
        api.service.transactions.journal_path.write_text("{incomplete")
        raise WorkflowMarketplaceError(
            "transaction_recovery_ambiguous", "private-recovery-location"
        )

    monkeypatch.setattr(api.service, "confirm_install", ambiguous)
    response = api.post("/install/confirm", request)
    assert response.status_code == 202
    operation = api.wait(response.json()["id"])
    assert operation["state"] == "failed"
    assert operation["outcome"] == {
        "type": "recovery_required",
        "reason": "recovery_ambiguous",
    }
    assert api.service.installed_packages()[0].version == "1.0.0"
    state = api.client.get(V2 + "/packages/company/laptop-support/state").json()
    assert state["state"] == "unconfirmed"
    assert state["installed"] is None and state["trust"] is None
    assert "private-recovery-location" not in json.dumps(operation)
