from __future__ import annotations

import importlib.util
import json
import logging
from contextlib import contextmanager
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hermes_cli.dashboard_auth.base import TokenPrincipal
from pydantic import ValidationError
import pytest
import yaml

from plugins.workflow.compat import assess_compatibility
from plugins.workflow.catalog_api import (
    resolve_workflow_catalog_compilation,
    workflow_catalog_run_support,
)
import plugins.workflow.showcase as showcase_module
from plugins.workflow.schema import load_workflow
from plugins.workflow.trust import (
    WorkflowTrustError,
    WorkflowTrustStore,
    build_risk_summary,
    compute_package_digest,
)


def _module():
    path = Path(__file__).parents[3] / "plugins/workflow/dashboard/plugin_api.py"
    spec = importlib.util.spec_from_file_location("workflow_catalog_api_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _app(router, *, token=None):
    app = FastAPI()

    @app.middleware("http")
    async def authenticated(request, call_next):
        if token is not None:
            request.state.token_principal = token
            request.state.token_authenticated = True
        return await call_next(request)

    app.include_router(router, prefix="/api/plugins/workflow")
    return app


def _reader() -> TokenPrincipal:
    return TokenPrincipal(
        principal="reader", provider="test", scopes=("workflow:read",)
    )


def _catalog_get(router, *, token=None):
    return TestClient(_app(router, token=token)).get("/api/plugins/workflow/workflows")


def _detail_get(router, name: str, *, source: str, token=None):
    return TestClient(_app(router, token=token)).get(
        f"/api/plugins/workflow/workflows/{name}",
        params={"catalog_source": source},
    )


def _user_items(response) -> list[dict[str, object]]:
    return [
        item
        for item in response.json()["items"]
        if item.get("source") != "showcase"
    ]


@contextmanager
def _test_bundle_path(root: Path):
    yield root.resolve()


def _restamp_showcase_package(root: Path, showcase_id: str) -> None:
    manifest_path = root / "digests.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["packages"][showcase_id] = showcase_module._tree_digest(
        root / "packages" / showcase_id
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_workflow_catalog_requires_verified_authentication() -> None:
    response = _catalog_get(_module().router)

    assert response.status_code == 401
    assert response.json() == {"detail": {"code": "authentication_required"}}


def test_workflow_catalog_requires_read_capability() -> None:
    token = TokenPrincipal(principal="writer", provider="test", scopes=())

    response = _catalog_get(_module().router, token=token)

    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "workflow_read_required"}}


def test_workflow_catalog_lists_verified_showcases_with_honest_support_and_compatibility(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(home / "workflows", name="ordinary-user-workflow")
    showcase_module._clear_verified_showcase_cache_for_tests()

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    payload = response.json()
    rows = {
        (item.get("source"), item.get("name")): item
        for item in payload["items"]
    }
    assert ("profile", "ordinary-user-workflow") in rows
    assert rows[("profile", "ordinary-user-workflow")]["language"] == {
        "effective_profile": "hermes-legacy",
        "legacy": True,
    }
    assert rows[("profile", "ordinary-user-workflow")]["compatibility"] == {
        "level": "mapped",
        "runnable": True,
    }
    showcase_rows = {
        name: row for (source, name), row in rows.items() if source == "showcase"
    }
    assert set(showcase_rows) == {
        "ai-extensions",
        "approval-gate",
        "laptop-diagnostic",
        "resilience",
        "scheduling",
    }
    approval = showcase_rows["approval-gate"]
    assert approval["language"] == {
        "effective_profile": "hermes-legacy",
        "legacy": True,
    }
    assert approval["trust_state"] == "verified_bundled"
    assert approval["supported_inputs"] == {
        "supported": True,
        "reason": "parameterless",
    }
    assert approval["run_support"] == {
        "supported": True,
        "reason": "supported",
    }
    laptop = showcase_rows["laptop-diagnostic"]
    assert laptop["inputs"] == [
        {"name": "evidence", "type": "file", "required": True},
        {"name": "symptom", "type": "text", "required": True, "max_bytes": 4096},
    ]
    assert laptop["supported_inputs"] == {
        "supported": True,
        "reason": "flat_inputs",
    }
    support_table = {
        name: row["run_support"] for name, row in showcase_rows.items()
    }
    assert support_table == {
        "approval-gate": {"supported": True, "reason": "supported"},
        "laptop-diagnostic": {"supported": True, "reason": "supported"},
        "resilience": {"supported": True, "reason": "supported"},
        "ai-extensions": {"supported": True, "reason": "supported"},
        "scheduling": {
            "supported": False,
            "reason": "schedule_required",
        },
    }
    assert sum(row["supported"] for row in support_table.values()) == 4
    assert showcase_rows["ai-extensions"]["compatibility"]["runnable"] is False
    assert payload["truncated"] is False

    scheduling_detail = _detail_get(
        _module().router,
        "scheduling",
        source="showcase",
        token=_reader(),
    )
    assert scheduling_detail.status_code == 200
    assert scheduling_detail.json()["run_support"] == {
        "supported": False,
        "reason": "schedule_required",
    }


@pytest.mark.parametrize("schedule_at", [None, "2099-01-02T03:04:05Z"])
def test_scheduled_run_support_retains_generic_showcase_network_policy(
    tmp_path, workflow_writer, schedule_at
) -> None:
    workflow = workflow_writer(
        tmp_path / "workflows",
        name="networked-schedule-policy-fixture",
    )
    package = load_workflow(workflow)
    scenario = replace(
        showcase_module.load_showcase_catalog()["scheduling"],
        id="non-catalog-networked-schedule",
        requires_network=True,
    )

    assert workflow_catalog_run_support(
        package,
        showcase_scenario=scenario,
        schedule_at=schedule_at,
    ) == {
        "supported": False,
        "reason": "showcase_cli_required",
    }


def test_workflow_catalog_projects_authenticated_requires_ai_for_every_row(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(home / "workflows", name="ordinary-user-workflow")
    showcase_module._clear_verified_showcase_cache_for_tests()

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    rows = {
        (item.get("source"), item.get("name")): item
        for item in response.json()["items"]
    }
    assert rows[("profile", "ordinary-user-workflow")]["requires_ai"] is False
    assert {
        name: row["requires_ai"]
        for (source, name), row in rows.items()
        if source == "showcase"
    } == {
        "ai-extensions": True,
        "approval-gate": False,
        "laptop-diagnostic": False,
        "resilience": False,
        "scheduling": False,
    }


def test_workflow_detail_and_response_model_require_generic_requires_ai(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(home / "workflows", name="ordinary-user-workflow")
    showcase_module._clear_verified_showcase_cache_for_tests()
    module = _module()

    ai = _detail_get(
        module.router,
        "ai-extensions",
        source="showcase",
        token=_reader(),
    )
    ordinary = _detail_get(
        module.router,
        "ordinary-user-workflow",
        source="profile",
        token=_reader(),
    )

    assert ai.status_code == ordinary.status_code == 200
    assert ai.json()["requires_ai"] is True
    assert ordinary.json()["requires_ai"] is False
    validated = module.WorkflowCatalogEntry.model_validate(
        next(
            item
            for item in _catalog_get(module.router, token=_reader()).json()["items"]
            if item.get("source") == "showcase"
        )
    )
    assert isinstance(validated.requires_ai, bool)
    with pytest.raises(ValidationError):
        module.WorkflowCatalogEntry.model_validate(
            {
                key: value
                for key, value in validated.model_dump().items()
                if key != "requires_ai"
            }
        )


def test_workflow_language_response_models_reject_non_contract_shapes() -> None:
    module = _module()
    list_status = {
        "effective_profile": "hermes-legacy",
        "legacy": True,
    }
    detail_status = {
        "declared_profile": "archon-2026-07",
        "effective_profile": "archon-2026-07",
        "legacy": False,
        "normalizer_version": 1,
        "normalized_definition_digest": "a" * 64,
    }

    assert module.WorkflowCatalogLanguageStatus.model_validate(list_status)
    assert module.WorkflowDetailLanguageStatus.model_validate(detail_status)
    assert module.WorkflowDetailLanguageStatus.model_validate(
        {**detail_status, "normalizer_version": 2}
    )
    assert module.WorkflowDetailLanguageStatus.model_validate(
        {**detail_status, "normalizer_version": 3}
    )

    invalid_list = [
        {**list_status, "declared_profile": None},
        {"legacy": True},
        {**list_status, "effective_profile": "future-profile"},
        {**list_status, "legacy": 1},
        {**list_status, "legacy": False},
    ]
    invalid_detail = [
        {**detail_status, "extra": "escape"},
        {key: value for key, value in detail_status.items() if key != "declared_profile"},
        {**detail_status, "declared_profile": "future-profile"},
        {**detail_status, "effective_profile": "hermes-legacy"},
        {**detail_status, "legacy": 0},
        {**detail_status, "normalizer_version": True},
        {**detail_status, "normalizer_version": 5},
        {**detail_status, "normalized_definition_digest": "A" * 64},
        {**detail_status, "normalized_definition_digest": "a" * 63},
    ]

    for payload in invalid_list:
        with pytest.raises(ValidationError):
            module.WorkflowCatalogLanguageStatus.model_validate(payload)
    for payload in invalid_detail:
        with pytest.raises(ValidationError):
            module.WorkflowDetailLanguageStatus.model_validate(payload)


def test_workflow_compatibility_response_models_are_exact_bounded_and_strict() -> None:
    module = _module()
    summary = {"level": "mapped", "runnable": True}
    finding = {
        "path": "nodes[0].timeout",
        "level": "unsupported",
        "message": "timeout semantics are unavailable",
        "blocking": True,
        "code": "archon_timeout_semantics_unavailable",
    }
    full = {
        "level": "unsupported",
        "runnable": False,
        "findings": [finding],
        "findings_truncated": False,
        "finding_count": 1,
    }

    assert module.WorkflowCompatibilitySummary.model_validate(summary)
    assert module.WorkflowCompatibilityFull.model_validate(full)

    invalid_summaries = [
        {**summary, "findings": []},
        {"level": "mapped"},
        {**summary, "level": "future"},
        {**summary, "runnable": 1},
    ]
    invalid_full = [
        {**full, "unknown": True},
        {
            **full,
            "findings": [finding]
            * (module.WORKFLOW_COMPATIBILITY_FINDINGS_MAX + 1),
        },
        {**full, "findings": [{**finding, "path": ""}]},
        {**full, "findings": [{**finding, "message": ""}]},
        {**full, "findings": [{**finding, "code": ""}]},
        {**full, "findings": [{**finding, "path": "p" * 16_385}]},
        {**full, "findings": [{**finding, "message": "m" * 16_385}]},
        {**full, "findings": [{**finding, "code": "c" * 16_385}]},
        {**full, "findings": [{**finding, "blocking": 1}]},
        {**full, "findings": [{**finding, "severity": "error"}]},
        {
            **full,
            "findings": [{**finding, "effective_profile": "archon-2026-07"}],
        },
        {**full, "findings": [{**finding, "migration": "Remove timeout."}]},
        {**full, "findings": [{**finding, "extra": "escape"}]},
        {**full, "findings_truncated": 0},
        {**full, "finding_count": True},
        {**full, "finding_count": 0},
        {**full, "findings_truncated": True},
    ]

    for payload in invalid_summaries:
        with pytest.raises(ValidationError):
            module.WorkflowCompatibilitySummary.model_validate(payload)
    for payload in invalid_full:
        with pytest.raises(ValidationError):
            module.WorkflowCompatibilityFull.model_validate(payload)


def test_workflow_compatibility_full_enforces_authoritative_report_state() -> None:
    module = _module()
    mapped = {
        "path": "nodes[0].model",
        "level": "mapped",
        "message": "model resolves through Hermes provider profiles",
        "blocking": False,
        "code": "provider_profile_resolution",
    }
    blocking = {
        "path": "nodes[0].timeout",
        "level": "unsupported",
        "message": "timeout semantics are unavailable",
        "blocking": True,
        "code": "archon_timeout_semantics_unavailable",
    }
    nonblocking_unsupported = {
        "path": "legacy_extension",
        "level": "unsupported",
        "message": "unknown top-level field",
        "blocking": False,
        "code": "unknown_top_level_field",
    }
    truncation_sentinel = {
        "path": "compatibility.findings",
        "level": "unsupported",
        "message": (
            "Compatibility findings truncated: 8 omitted; aggregate level unsupported"
        ),
        "blocking": True,
        "code": "compatibility_findings_truncated",
    }
    retained = [{**mapped, "path": f"mapped[{index}]"} for index in range(511)]

    valid = [
        {
            "level": "portable",
            "runnable": True,
            "findings": [],
            "findings_truncated": False,
            "finding_count": 0,
        },
        {
            "level": "mapped",
            "runnable": True,
            "findings": [mapped],
            "findings_truncated": False,
            "finding_count": 1,
        },
        {
            "level": "unsupported",
            "runnable": False,
            "findings": [blocking],
            "findings_truncated": False,
            "finding_count": 1,
        },
        {
            "level": "unsupported",
            "runnable": True,
            "findings": [nonblocking_unsupported],
            "findings_truncated": False,
            "finding_count": 1,
        },
        {
            "level": "unsupported",
            "runnable": False,
            "findings": [*retained, truncation_sentinel],
            "findings_truncated": True,
            "finding_count": 519,
        },
    ]
    invalid = [
        {
            "level": "unsupported",
            "runnable": True,
            "findings": [],
            "findings_truncated": False,
            "finding_count": 0,
        },
        {
            "level": "portable",
            "runnable": False,
            "findings": [blocking],
            "findings_truncated": False,
            "finding_count": 1,
        },
        {
            "level": "portable",
            "runnable": True,
            "findings": [mapped],
            "findings_truncated": False,
            "finding_count": 1,
        },
        {
            "level": "mapped",
            "runnable": True,
            "findings": [],
            "findings_truncated": False,
            "finding_count": 0,
        },
        {
            "level": "mapped",
            "runnable": False,
            "findings": [mapped],
            "findings_truncated": False,
            "finding_count": 1,
        },
        {
            "level": "mapped",
            "runnable": True,
            "findings": [nonblocking_unsupported],
            "findings_truncated": False,
            "finding_count": 1,
        },
        {
            "level": "unsupported",
            "runnable": True,
            "findings": [blocking],
            "findings_truncated": False,
            "finding_count": 1,
        },
        {
            "level": "unsupported",
            "runnable": False,
            "findings": [*retained, truncation_sentinel],
            "findings_truncated": True,
            "finding_count": 518,
        },
    ]

    for payload in valid:
        module.WorkflowCompatibilityFull.model_validate(payload)
    for payload in invalid:
        with pytest.raises(ValidationError):
            module.WorkflowCompatibilityFull.model_validate(payload)


def test_workflow_compatibility_full_rejects_malformed_truncation_sentinels() -> None:
    module = _module()
    mapped = {
        "path": "nodes[0].model",
        "level": "mapped",
        "message": "model resolves through Hermes provider profiles",
        "blocking": False,
        "code": "provider_profile_resolution",
    }
    retained = [{**mapped, "path": f"mapped[{index}]"} for index in range(511)]

    def sentinel(*, level: str, blocking: bool, count: str) -> dict[str, object]:
        return {
            "path": "compatibility.findings",
            "level": level,
            "message": (
                f"Compatibility findings truncated: {count} omitted; "
                f"aggregate level {level}"
            ),
            "blocking": blocking,
            "code": "compatibility_findings_truncated",
        }

    invalid = [
        {
            "level": "unsupported",
            "runnable": True,
            "findings": [
                *retained,
                sentinel(level="unsupported", blocking=False, count="0"),
            ],
            "findings_truncated": True,
            "finding_count": 511,
        },
        {
            "level": "unsupported",
            "runnable": True,
            "findings": [
                *retained,
                sentinel(level="unsupported", blocking=False, count="01"),
            ],
            "findings_truncated": True,
            "finding_count": 512,
        },
        {
            "level": "unsupported",
            "runnable": True,
            "findings": [sentinel(level="unsupported", blocking=False, count="1")],
            "findings_truncated": True,
            "finding_count": 1,
        },
        {
            "level": "mapped",
            "runnable": True,
            "findings": [
                *retained,
                sentinel(level="portable", blocking=False, count="1"),
            ],
            "findings_truncated": True,
            "finding_count": 512,
        },
        {
            "level": "unsupported",
            "runnable": False,
            "findings": [
                *retained,
                sentinel(level="mapped", blocking=True, count="1"),
            ],
            "findings_truncated": True,
            "finding_count": 512,
        },
    ]

    for payload in invalid:
        with pytest.raises(ValidationError):
            module.WorkflowCompatibilityFull.model_validate(payload)


def test_workflow_compatibility_full_accepts_only_complete_legacy_shape() -> None:
    module = _module()
    mapped = {
        "path": "nodes[0].model",
        "level": "mapped",
        "message": "model resolves through Hermes provider profiles",
        "blocking": False,
        "code": "provider_profile_resolution",
    }
    complete_legacy = {
        "level": "mapped",
        "runnable": True,
        "findings": [mapped],
    }

    validated = module.WorkflowCompatibilityFull.model_validate(complete_legacy)

    assert validated.findings_truncated is False
    assert validated.finding_count == 1
    for partial in (
        {**complete_legacy, "findings_truncated": False},
        {**complete_legacy, "finding_count": 1},
    ):
        with pytest.raises(ValidationError):
            module.WorkflowCompatibilityFull.model_validate(partial)

    truncated_legacy = {
        "level": "unsupported",
        "runnable": True,
        "findings": [
            *({**mapped, "path": f"mapped[{index}]"} for index in range(511)),
            {
                "path": "compatibility.findings",
                "level": "unsupported",
                "message": (
                    "Compatibility findings truncated: 1 omitted; "
                    "aggregate level unsupported"
                ),
                "blocking": False,
                "code": "compatibility_findings_truncated",
            },
        ],
    }
    with pytest.raises(ValidationError):
        module.WorkflowCompatibilityFull.model_validate(truncated_legacy)


def test_workflow_compatibility_full_rejects_payload_at_one_mib() -> None:
    module = _module()
    retained = [
        {
            "path": f"mapped[{index}]" + "p" * 16_000,
            "level": "mapped",
            "message": "m" * 16_000,
            "blocking": False,
            "code": "mapped_finding",
        }
        for index in range(511)
    ]
    sentinel = {
        "path": "compatibility.findings",
        "level": "mapped",
        "message": (
            "Compatibility findings truncated: 1 omitted; aggregate level mapped"
        ),
        "blocking": False,
        "code": "compatibility_findings_truncated",
    }

    with pytest.raises(ValidationError):
        module.WorkflowCompatibilityFull.model_validate({
            "level": "mapped",
            "runnable": True,
            "findings": [*retained, sentinel],
            "findings_truncated": True,
            "finding_count": 512,
        })


def test_workflow_compatibility_finding_api_projection_normalizes_only_empty_paths() -> None:
    module = _module()
    base = {
        "level": "unsupported",
        "message": "unknown top-level field: raw",
        "blocking": False,
        "code": "unknown_top_level_field",
    }

    legitimate = module._sanitize_compatibility_finding_projection(
        {**base, "path": "nodes[0].timeout"}
    )
    assert legitimate["path"] == "nodes[0].timeout"
    module.WorkflowCompatibilityFinding.model_validate(legitimate)

    absolute = module._sanitize_compatibility_finding_projection(
        {**base, "path": "/private/tmp/operator-secret.yaml"}
    )
    ansi_prefixed = module._sanitize_compatibility_finding_projection(
        {**base, "path": "\x1b[31mnodes[0].timeout"}
    )
    assert absolute["path"] == "operator-secret.yaml"
    assert ansi_prefixed["path"] == "nodes[0].timeout"
    module.WorkflowCompatibilityFinding.model_validate(absolute)
    module.WorkflowCompatibilityFinding.model_validate(ansi_prefixed)

    for raw_path in ("", "/", ".", "\x1b[31m"):
        projected = module._sanitize_compatibility_finding_projection(
            {
                **base,
                "path": raw_path,
                "message": f"unknown top-level field: {raw_path}",
            }
        )
        assert projected == {
            **base,
            "path": module.WORKFLOW_COMPATIBILITY_UNKNOWN_PATH,
            "message": (
                "unknown top-level field: "
                f"{module.WORKFLOW_COMPATIBILITY_UNKNOWN_PATH}"
            ),
        }
        module.WorkflowCompatibilityFinding.model_validate(projected)


def test_workflow_catalog_response_model_enforces_source_projection_and_old_optional_compatibility() -> None:
    module = _module()
    base = {
        "name": "response-model",
        "version": "1",
        "description": "Response model fixture",
        "requires_ai": False,
        "source": "profile",
        "precedence": 2,
        "trust_state": "untrusted",
        "inputs": [],
        "supported_inputs": {"supported": True, "reason": "parameterless"},
        "run_support": {"supported": True, "reason": "supported"},
        "language": {"effective_profile": "hermes-legacy", "legacy": True},
    }
    summary = {"level": "mapped", "runnable": True}
    full = {
        "level": "portable",
        "runnable": True,
        "findings": [],
        "findings_truncated": False,
        "finding_count": 0,
    }

    assert module.WorkflowCatalogEntry.model_validate({**base, "compatibility": summary})
    assert module.WorkflowCatalogEntry.model_validate(base).compatibility is None
    with pytest.raises(ValidationError):
        module.WorkflowCatalogEntry.model_validate({**base, "compatibility": full})

    showcase = {
        **base,
        "source": "showcase",
        "precedence": 3,
        "trust_state": "verified_bundled",
    }
    assert module.WorkflowCatalogEntry.model_validate(
        {**showcase, "compatibility": full}
    )
    with pytest.raises(ValidationError):
        module.WorkflowCatalogEntry.model_validate(
            {**showcase, "compatibility": summary}
        )


def test_workflow_catalog_openapi_closes_language_and_compatibility_objects() -> None:
    module = _module()
    schemas = _app(module.router, token=_reader()).openapi()["components"]["schemas"]
    nested_names = {
        "WorkflowCatalogLanguageStatus",
        "WorkflowDetailLanguageStatus",
        "WorkflowCompatibilitySummary",
        "WorkflowCompatibilityFull",
        "WorkflowCompatibilityFinding",
    }

    for name in nested_names:
        schema = schemas[name]
        assert schema["additionalProperties"] is False
        assert schema["type"] == "object"
        assert schema.get("additionalProperties") != {}
        assert schema.get("additionalProperties") is not True

    finding = schemas["WorkflowCompatibilityFinding"]
    assert set(finding["properties"]) == {
        "path",
        "level",
        "message",
        "blocking",
        "code",
    }
    assert set(finding["required"]) == set(finding["properties"])
    assert finding["properties"]["path"]["minLength"] == 1
    assert finding["properties"]["message"]["minLength"] == 1
    assert finding["properties"]["code"]["minLength"] == 1
    assert (
        schemas["WorkflowCompatibilityFull"]["properties"]["findings"]["maxItems"]
        == module.WORKFLOW_COMPATIBILITY_FINDINGS_MAX
    )
    assert set(schemas["WorkflowCompatibilityFull"]["properties"]) == {
        "level",
        "runnable",
        "findings",
        "findings_truncated",
        "finding_count",
    }


def test_workflow_catalog_keeps_isolation_incompatibility_scenario_local(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(home / "workflows", name="ordinary-user-workflow")
    copied = tmp_path / "showcases"
    shutil.copytree(Path(showcase_module.__file__).with_name("showcases"), copied)
    sidecar = (
        copied
        / "packages"
        / "approval-gate"
        / "workflows"
        / "approval-gate.hermes.yaml"
    )
    sidecar.write_text(
        sidecar.read_text(encoding="utf-8").replace(
            "execution_environment: trusted_local",
            "execution_environment: isolated_backend_required",
        ),
        encoding="utf-8",
    )
    _restamp_showcase_package(copied, "approval-gate")
    showcase_module._clear_verified_showcase_cache_for_tests()
    monkeypatch.setattr(
        showcase_module,
        "_bundle_path",
        lambda explicit=None: _test_bundle_path(copied),
    )

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    rows = {
        item["name"]: item
        for item in response.json()["items"]
        if item.get("source") == "showcase"
    }
    assert set(rows) == {
        "ai-extensions",
        "approval-gate",
        "laptop-diagnostic",
        "resilience",
        "scheduling",
    }
    assert rows["approval-gate"]["compatibility"]["runnable"] is False
    assert rows["approval-gate"]["compatibility"]["findings"] == [
        {
            "blocking": False,
            "code": "legacy_language_profile",
            "level": "mapped",
            "message": "workflow uses permissive Hermes legacy language semantics",
            "path": "sidecar.language_compatibility",
        },
        {
            "blocking": True,
            "code": "execution_environment_unavailable",
            "level": "unsupported",
            "message": "workflow requires a configured isolated backend",
            "path": "sidecar.execution_environment",
        }
    ]


def test_workflow_catalog_cached_showcase_verification_preserves_user_budget(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(home / "workflows", name="ordinary-user-workflow")
    showcase_module._clear_verified_showcase_cache_for_tests()
    calls = 0
    original = showcase_module._tree_digest

    def counted_tree_digest(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(showcase_module, "_tree_digest", counted_tree_digest)

    first = _catalog_get(_module().router, token=_reader())
    calls_after_first = calls
    second = _catalog_get(_module().router, token=_reader())

    assert first.status_code == second.status_code == 200
    assert calls_after_first > 0
    assert calls == calls_after_first
    for response in (first, second):
        payload = response.json()
        assert payload["truncated"] is False
        assert any(
            item.get("source") == "profile"
            and item.get("name") == "ordinary-user-workflow"
            for item in payload["items"]
        )


def test_workflow_catalog_projects_showcase_from_authenticated_snapshot(
    tmp_path, monkeypatch
) -> None:
    import plugins.workflow.catalog_api as catalog_api

    copied = tmp_path / "showcases"
    shutil.copytree(Path(showcase_module.__file__).with_name("showcases"), copied)
    showcase_module._clear_verified_showcase_cache_for_tests()
    monkeypatch.setattr(
        showcase_module,
        "_bundle_path",
        lambda explicit=None: _test_bundle_path(copied),
    )
    target = copied / "packages/ai-extensions/commands/inspect-evidence.md"
    original_loader = showcase_module.load_verified_showcase_packages
    original_open = Path.open
    original_stat = Path.stat
    projection_started = False
    authenticated_digest = ""
    projected_digests: list[str] = []

    def mutate_after_authentication(*args, **kwargs):
        nonlocal projection_started, authenticated_digest
        verified = original_loader(*args, **kwargs)
        authenticated_digest = verified["ai-extensions"].package_digest
        target.write_text("mutated after authentication\n", encoding="utf-8")
        projection_started = True
        return verified

    def forbid_reopen(self, *args, **kwargs):
        if projection_started and self == target:
            pytest.fail("catalog projection reopened authenticated showcase source")
        return original_open(self, *args, **kwargs)

    def forbid_restat(self, *args, **kwargs):
        if projection_started and self == target:
            pytest.fail("catalog projection restatted authenticated showcase source")
        return original_stat(self, *args, **kwargs)

    original_assess = catalog_api.assess_package_execution

    def capture_assessment(package, context, *, read_budget=None):
        compatibility, risk = original_assess(
            package,
            context,
            read_budget=read_budget,
        )
        if package.source == "showcase" and package.definition.name == "ai-extensions":
            projected_digests.append(risk.package_digest)
        return compatibility, risk

    monkeypatch.setattr(
        showcase_module,
        "load_verified_showcase_packages",
        mutate_after_authentication,
    )
    monkeypatch.setattr(Path, "open", forbid_reopen)
    monkeypatch.setattr(Path, "stat", forbid_restat)
    monkeypatch.setattr(catalog_api, "assess_package_execution", capture_assessment)

    items, truncated = catalog_api.build_workflow_catalog(
        hermes_home=tmp_path / "home",
        workdir=tmp_path,
    )

    assert truncated is False
    assert projected_digests == [authenticated_digest]
    ai_row = next(item for item in items if item["name"] == "ai-extensions")
    assert ai_row["trust_state"] == "verified_bundled"


def test_workflow_catalog_projects_cached_showcase_when_source_disappears_after_authentication(
    tmp_path, monkeypatch, workflow_writer, caplog
) -> None:
    import plugins.workflow.catalog_api as catalog_api

    home = tmp_path / "home"
    workflow_writer(home / "workflows", name="ordinary-user-workflow")
    copied = tmp_path / "showcases"
    shutil.copytree(Path(showcase_module.__file__).with_name("showcases"), copied)
    showcase_module._clear_verified_showcase_cache_for_tests()
    monkeypatch.setattr(
        showcase_module,
        "_bundle_path",
        lambda explicit=None: _test_bundle_path(copied),
    )
    target = copied / "packages/ai-extensions/commands/inspect-evidence.md"
    original_loader = showcase_module.load_verified_showcase_packages

    def delete_after_authentication(*args, **kwargs):
        verified = original_loader(*args, **kwargs)
        target.unlink()
        return verified

    monkeypatch.setattr(
        showcase_module,
        "load_verified_showcase_packages",
        delete_after_authentication,
    )
    caplog.set_level(logging.WARNING, logger="plugins.workflow.catalog_api")

    items, truncated = catalog_api.build_workflow_catalog(
        hermes_home=home,
        workdir=tmp_path,
    )

    assert truncated is False
    ai_row = next(
        item
        for item in items
        if item.get("source") == "showcase" and item.get("name") == "ai-extensions"
    )
    assert ai_row["trust_state"] == "verified_bundled"
    assert any(
        item.get("source") == "profile"
        and item.get("name") == "ordinary-user-workflow"
        for item in items
    )
    assert [
        record.getMessage()
        for record in caplog.records
        if record.name == "plugins.workflow.catalog_api"
        and record.levelno == logging.WARNING
    ] == []


def test_workflow_catalog_rejects_verified_provenance_on_digest_mismatch(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    import plugins.workflow.catalog_api as catalog_api

    home = tmp_path / "home"
    workflow_writer(home / "workflows", name="ordinary-user-workflow")
    showcase_module._clear_verified_showcase_cache_for_tests()
    original_assess = catalog_api.assess_package_execution

    def corrupt_showcase_digest(package, context, *, read_budget=None):
        compatibility, risk = original_assess(
            package,
            context,
            read_budget=read_budget,
        )
        if package.source == "showcase":
            risk = replace(risk, package_digest="0" * 64)
        return compatibility, risk

    monkeypatch.setattr(
        catalog_api,
        "assess_package_execution",
        corrupt_showcase_digest,
    )

    items, truncated = catalog_api.build_workflow_catalog(
        hermes_home=home,
        workdir=tmp_path,
    )

    assert truncated is False
    assert not any(item.get("source") == "showcase" for item in items)
    assert any(
        item.get("source") == "profile"
        and item.get("name") == "ordinary-user-workflow"
        for item in items
    )


def test_workflow_catalog_missing_bundle_degrades_to_user_rows(
    tmp_path, monkeypatch, workflow_writer, caplog
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(home / "workflows", name="ordinary-user-workflow")
    missing = tmp_path / "missing-showcases"
    showcase_module._clear_verified_showcase_cache_for_tests()
    monkeypatch.setattr(
        showcase_module,
        "_bundle_path",
        lambda explicit=None: _test_bundle_path(missing),
    )
    caplog.set_level(logging.INFO, logger="plugins.workflow.catalog_api")

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    assert [
        item["name"]
        for item in response.json()["items"]
        if item.get("source") == "profile"
    ] == ["ordinary-user-workflow"]
    assert not any(
        item.get("source") == "showcase" for item in response.json()["items"]
    )
    signals = [
        record.getMessage()
        for record in caplog.records
        if record.name == "plugins.workflow.catalog_api"
        and record.levelno == logging.INFO
    ]
    assert signals == [
        "workflow showcase catalog verification unavailable: FileNotFoundError"
    ]


def test_workflow_catalog_tamper_invalidates_cache_and_omits_entire_bundle(
    tmp_path, monkeypatch, workflow_writer, caplog
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(home / "workflows", name="ordinary-user-workflow")
    copied = tmp_path / "showcases"
    shutil.copytree(Path(showcase_module.__file__).with_name("showcases"), copied)
    showcase_module._clear_verified_showcase_cache_for_tests()
    monkeypatch.setattr(
        showcase_module,
        "_bundle_path",
        lambda explicit=None: _test_bundle_path(copied),
    )
    import hermes_cli.capability_staging as capability_staging

    monkeypatch.setattr(
        capability_staging,
        "repair_authenticated_resource_checkout",
        lambda *_args, **_kwargs: pytest.fail("HTTP catalog attempted checkout repair"),
    )

    first = _catalog_get(_module().router, token=_reader())
    assert first.status_code == 200
    assert sum(
        item.get("source") == "showcase" for item in first.json()["items"]
    ) == 5
    workflow = (
        copied
        / "packages"
        / "approval-gate"
        / "workflows"
        / "approval-gate.yaml"
    )
    workflow.write_text(workflow.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    caplog.set_level(logging.WARNING, logger="plugins.workflow.catalog_api")
    caplog.clear()

    second = _catalog_get(_module().router, token=_reader())

    assert second.status_code == 200
    assert not any(
        item.get("source") == "showcase" for item in second.json()["items"]
    )
    assert any(
        item.get("source") == "profile"
        and item.get("name") == "ordinary-user-workflow"
        for item in second.json()["items"]
    )
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.name == "plugins.workflow.catalog_api"
        and record.levelno == logging.WARNING
    ]
    assert warnings == [
        "workflow showcase catalog verification failed: ShowcaseCatalogError"
    ]


def test_workflow_catalog_and_detail_project_inputs_once_per_row(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "repo"
    workdir.mkdir()
    workflow_writer(home / "workflows", name="alpha", filename="alpha.yaml")
    workflow_writer(home / "workflows", name="bravo", filename="bravo.yaml")
    missing = tmp_path / "missing-showcases"
    showcase_module._clear_verified_showcase_cache_for_tests()
    monkeypatch.setattr(
        showcase_module,
        "_bundle_path",
        lambda explicit=None: _test_bundle_path(missing),
    )
    import plugins.workflow.catalog_api as catalog_api

    calls = 0
    original_input_projection = catalog_api._input_projection

    def count_input_projection(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_input_projection(*args, **kwargs)

    monkeypatch.setattr(catalog_api, "_input_projection", count_input_projection)
    monkeypatch.setattr(
        catalog_api,
        "_coordinator_projection",
        lambda _home: {"healthy": False, "status": "unavailable", "reason": "test"},
    )

    items, truncated = catalog_api.build_workflow_catalog(
        hermes_home=home,
        workdir=workdir,
    )

    assert truncated is False
    assert [item["name"] for item in items] == ["alpha", "bravo"]
    assert calls == 2

    catalog_api.build_workflow_detail(
        "alpha",
        hermes_home=home,
        workdir=workdir,
        catalog_source="profile",
    )

    assert calls == 3


@pytest.mark.parametrize(
    "description",
    ["/Users/example/private/workflow", r"C:\Users\example\private\workflow"],
)
def test_workflow_catalog_description_uses_definition_path_redaction(
    tmp_path, monkeypatch, workflow_writer, description
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(
        home / "workflows",
        name="path-description",
        description=description,
    )

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    row = next(
        item
        for item in response.json()["items"]
        if item.get("source") == "profile" and item.get("name") == "path-description"
    )
    assert row["description"] == "[REDACTED]"
    assert description.encode() not in response.content


def test_workflow_catalog_returns_stable_redacted_server_classification(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    beta = workflow_writer(
        home / "workflows",
        name="beta",
        description="Parameterless workflow",
        filename="beta.yaml",
    )
    alpha = workflow_writer(
        home / "workflows",
        name="alpha",
        description="Typed workflow",
        filename="alpha.yaml",
    )
    alpha.with_name("alpha.hermes.yaml").write_text(
        yaml.safe_dump(
            {
                "delivery_defaults": {
                    "inputs": {
                        "count": {
                            "type": "number",
                            "required": False,
                            "default": "SECRET_NUMERIC_DEFAULT",
                        },
                        "enabled": {
                            "type": "boolean",
                            "required": False,
                        },
                        "mode": {
                            "type": "enum",
                            "required": True,
                            "values": ["safe", "fast"],
                        },
                        "title": {
                            "type": "string",
                            "required": True,
                            "default": "SECRET_TITLE_DEFAULT",
                        },
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    package = load_workflow(alpha, source="profile", precedence=2)
    digest = compute_package_digest(package)
    risk = build_risk_summary(package, assess_compatibility(package))
    WorkflowTrustStore(home).trust(
        digest.sha256, actor="catalog-test", risk_digest=risk.risk_digest
    )

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    payload = response.json()
    assert payload["truncated"] is False
    user_items = _user_items(response)
    assert [item["name"] for item in user_items] == ["alpha", "beta"]
    assert user_items[0] == {
        "name": "alpha",
        "version": "1",
        "description": "Typed workflow",
        "requires_ai": False,
        "source": "profile",
        "precedence": 2,
        "trust_state": "trusted",
        "inputs": [
            {"name": "count", "type": "number", "required": False},
            {"name": "enabled", "type": "boolean", "required": False},
            {"name": "mode", "type": "enum", "required": True},
            {"name": "title", "type": "string", "required": True},
        ],
        "supported_inputs": {"supported": True, "reason": "flat_inputs"},
        "run_support": {"supported": True, "reason": "supported"},
        "language": {
            "effective_profile": "hermes-legacy",
            "legacy": True,
        },
        "compatibility": {"level": "mapped", "runnable": True},
    }
    assert user_items[1]["trust_state"] == "untrusted"
    assert user_items[1]["inputs"] == []
    assert user_items[1]["supported_inputs"] == {
        "supported": True,
        "reason": "parameterless",
    }
    assert user_items[1]["language"] == {
        "effective_profile": "hermes-legacy",
        "legacy": True,
    }
    assert user_items[1]["compatibility"] == {
        "level": "mapped",
        "runnable": True,
    }
    assert user_items[1]["run_support"] == {
        "supported": True,
        "reason": "supported",
    }
    assert b"SECRET_NUMERIC_DEFAULT" not in response.content
    assert b"SECRET_TITLE_DEFAULT" not in response.content
    assert beta.is_file()


def test_workflow_catalog_projects_archon_language_and_bounded_compatibility(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "project"
    workdir.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.chdir(workdir)
    path = workflow_writer(
        workdir / ".hermes" / "workflows",
        name="archon-deferred",
        filename="archon-deferred.yaml",
        nodes=[{"id": "start", "bash": "true", "timeout": 5}],
    )
    path.with_name("archon-deferred.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    row = next(
        item
        for item in response.json()["items"]
        if item.get("source") == "project" and item.get("name") == "archon-deferred"
    )
    assert row["language"] == {
        "effective_profile": "archon-2026-07",
        "legacy": False,
    }
    assert row["compatibility"] == {
        "level": "portable",
        "runnable": True,
    }
    assert set(row["language"]) == {"effective_profile", "legacy"}
    assert set(row["compatibility"]) == {"level", "runnable"}


def test_current_v4_composite_trust_is_consistent_between_catalog_and_detail(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "project"
    workdir.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.chdir(workdir)
    root = workflow_writer(
        workdir / ".hermes" / "workflows",
        name="catalog-v4-composite",
        filename="catalog-v4-composite.yaml",
        nodes=[{"id": "dependency", "include": "catalog-v4-child"}],
    )
    root.with_name("catalog-v4-composite.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    workflow_writer(
        home / "workflows",
        name="catalog-v4-child",
        filename="catalog-v4-child.yaml",
        nodes=[{"id": "execute", "bash": "true"}],
    )
    compilation = resolve_workflow_catalog_compilation(
        "catalog-v4-composite",
        hermes_home=home,
        workdir=workdir,
        catalog_source="project",
    )
    assert compilation is not None
    risk = build_risk_summary(
        compilation.package,
        assess_compatibility(compilation.package),
        compilation=compilation,
    )
    WorkflowTrustStore(home).trust(
        compilation.composite_digest,
        actor="catalog-v4-test",
        risk_digest=risk.risk_digest,
    )

    router = _module().router
    catalog_response = _catalog_get(router, token=_reader())
    detail_response = _detail_get(
        router,
        "catalog-v4-composite",
        source="project",
        token=_reader(),
    )

    assert catalog_response.status_code == detail_response.status_code == 200
    catalog_row = next(
        item
        for item in _user_items(catalog_response)
        if item["name"] == "catalog-v4-composite"
    )
    detail = detail_response.json()
    assert catalog_row["trust_state"] == detail["trust_state"] == "trusted"
    assert detail["risk_summary"]["package_digest"] == compilation.composite_digest
    assert detail["compilation"]["composite_digest"] == (
        compilation.composite_digest
    )


def test_workflow_detail_bounds_more_than_512_real_findings_and_keeps_omitted_blocker(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    unknown_fields = {f"future_{index:04d}": "x" * 32 for index in range(600)}
    workflow_writer(
        home / "workflows",
        name="bounded-findings",
        filename="bounded-findings.yaml",
        nodes=[
            {
                "id": "agent",
                "prompt": "bounded",
                "allowed_tools": ["UnknownTool"],
            }
        ],
        **unknown_fields,
    )

    response = _detail_get(
        _module().router,
        "bounded-findings",
        source="profile",
        token=_reader(),
    )

    assert response.status_code == 200
    assert len(response.content) < 1024 * 1024
    compatibility = response.json()["compatibility"]
    assert compatibility["level"] == "unsupported"
    assert compatibility["runnable"] is False
    assert compatibility["findings_truncated"] is True
    assert compatibility["finding_count"] == 602
    assert len(compatibility["findings"]) == 512
    sentinel = compatibility["findings"][-1]
    assert sentinel["code"] == "compatibility_findings_truncated"
    assert sentinel["level"] == "unsupported"
    assert sentinel["blocking"] is True


def test_workflow_detail_bounds_hostile_unicode_and_escaped_compatibility_payload(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    hostile_suffix = "😀" * 240
    unknown_fields = {
        f"future_{index:04d}_{hostile_suffix}": "x" for index in range(508)
    }
    unknown_fields.update({
        "future_control_" + "\x01" * 600: "x",
        "future_quote_" + '"' * 600: "x",
        "future_backslash_" + "\\" * 600: "x",
        "future_mixed_" + '😀\x01"\\' * 150: "x",
    })
    workflow_path = workflow_writer(
        home / "workflows",
        name="hostile-compatibility-payload",
        filename="hostile-compatibility-payload.yaml",
        **unknown_fields,
    )
    # Keep the valid definition below its independent read budget while retaining
    # astral Unicode that expands when repeated into compatibility path + message.
    document = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    workflow_path.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    response = _detail_get(
        _module().router,
        "hostile-compatibility-payload",
        source="profile",
        token=_reader(),
    )

    assert response.status_code == 200
    compatibility = response.json()["compatibility"]
    serialized_compatibility = json.dumps(
        compatibility,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(serialized_compatibility) < 1024 * 1024
    assert len(response.content) < 1024 * 1024
    assert compatibility["findings_truncated"] is True
    assert compatibility["finding_count"] == 513
    assert len(compatibility["findings"]) == 512


def test_workflow_catalog_projects_declared_text_bounds_and_support_modes(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    text_path = workflow_writer(
        home / "workflows", name="declared-text", filename="declared-text.yaml"
    )
    text_path.with_name("declared-text.hermes.yaml").write_text(
        yaml.safe_dump(
            {
                "delivery_defaults": {
                    "inputs": {
                        "notes": {
                            "kind": "text",
                            "required": False,
                            "max_bytes": 70 * 1024,
                            "default": "SECRET_NOTES_DEFAULT",
                        },
                        "summary": {
                            "kind": "text",
                            "required": True,
                            "max_bytes": 4096,
                            "default": "SECRET_SUMMARY_DEFAULT",
                        },
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    workflow_writer(home / "workflows", name="legacy-flat", filename="legacy-flat.yaml")
    file_path = workflow_writer(
        home / "workflows", name="declared-file", filename="declared-file.yaml"
    )
    file_path.with_name("declared-file.hermes.yaml").write_text(
        yaml.safe_dump(
            {
                "delivery_defaults": {
                    "inputs": {
                        "evidence": {
                            "kind": "file",
                            "required": True,
                            "max_bytes": 4096,
                        }
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    rows = {item["name"]: item for item in _user_items(response)}
    assert rows["declared-text"]["inputs"] == [
        {
            "name": "notes",
            "type": "text",
            "required": False,
            "max_bytes": 64 * 1024,
        },
        {
            "name": "summary",
            "type": "text",
            "required": True,
            "max_bytes": 4096,
        },
    ]
    assert rows["declared-text"]["supported_inputs"] == {
        "supported": True,
        "reason": "flat_inputs",
    }
    assert rows["declared-text"]["run_support"] == {
        "supported": True,
        "reason": "supported",
    }
    assert rows["legacy-flat"]["supported_inputs"] == {
        "supported": True,
        "reason": "parameterless",
    }
    assert rows["legacy-flat"]["run_support"] == {
        "supported": True,
        "reason": "supported",
    }
    assert rows["declared-file"]["inputs"] == [
        {"name": "evidence", "type": "file", "required": True}
    ]
    assert rows["declared-file"]["supported_inputs"] == {
        "supported": False,
        "reason": "unsupported_input_type",
    }
    assert rows["declared-file"]["run_support"] == {
        "supported": False,
        "reason": "unsupported_inputs",
    }
    assert b"SECRET_NOTES_DEFAULT" not in response.content
    assert b"SECRET_SUMMARY_DEFAULT" not in response.content


def test_workflow_catalog_marks_legacy_and_rich_input_kinds_unsupported(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    path = workflow_writer(home / "workflows", name="legacy-inputs")
    path.with_name("example.hermes.yaml").write_text(
        yaml.safe_dump(
            {
                "delivery_defaults": {
                    "inputs": {
                        "attachment": {"kind": "file", "required": True},
                        "metadata": {
                            "type": "object",
                            "required": False,
                            "properties": {"secret": {"type": "string"}},
                        },
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    item = _user_items(response)[0]
    assert item["inputs"] == [
        {"name": "attachment", "type": "file", "required": True},
        {"name": "metadata", "type": "object", "required": False},
    ]
    assert item["supported_inputs"] == {
        "supported": False,
        "reason": "unsupported_input_type",
    }
    assert item["run_support"] == {
        "supported": False,
        "reason": "unsupported_inputs",
    }
    assert b"properties" not in response.content
    assert b"secret" not in response.content


def test_workflow_catalog_marks_enum_without_usable_choices_unsupported(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    path = workflow_writer(home / "workflows", name="empty-enum")
    path.with_name("example.hermes.yaml").write_text(
        yaml.safe_dump(
            {
                "delivery_defaults": {
                    "inputs": {
                        "mode": {
                            "type": "enum",
                            "required": False,
                        }
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    item = _user_items(response)[0]
    assert item["inputs"] == [{"name": "mode", "type": "enum", "required": False}]
    assert item["supported_inputs"] == {
        "supported": False,
        "reason": "unsupported_input_shape",
    }
    assert item["run_support"] == {
        "supported": False,
        "reason": "unsupported_inputs",
    }


def test_workflow_catalog_keeps_missing_input_kind_unsupported(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    path = workflow_writer(home / "workflows", name="missing-input-kind")
    path.with_name("example.hermes.yaml").write_text(
        yaml.safe_dump(
            {
                "delivery_defaults": {
                    "inputs": {
                        "subject": {
                            "required": True,
                            "max_bytes": 32,
                            "default": "SECRET_MISSING_KIND_DEFAULT",
                        }
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    item = _user_items(response)[0]
    assert item["inputs"] == [{"name": "subject", "type": "unknown", "required": True}]
    assert item["supported_inputs"] == {
        "supported": False,
        "reason": "unsupported_input_type",
    }
    assert item["run_support"] == {
        "supported": False,
        "reason": "unsupported_inputs",
    }
    assert b"SECRET_MISSING_KIND_DEFAULT" not in response.content


@pytest.mark.parametrize(
    "input_name",
    [
        "   ",
        "x" * 129,
        "foo/bar",
        "foo\\bar",
        "mode\x1b[31m",
        "api_token",
        "CON",
        "nul.txt",
        "COM1",
        "foo:bar",
        "a?b",
        "a*b",
        "<x>",
        "a|b",
        "trailing.",
        "trailing ",
        "😀" * 64,
        "COM¹",
        "COM².txt",
        "com³",
        "LPT¹",
        "lpt².log",
        "LPT³",
    ],
)
def test_workflow_catalog_rejects_unrepresentable_input_names_without_renaming(
    tmp_path, monkeypatch, workflow_writer, input_name
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    path = workflow_writer(home / "workflows", name="invalid-input-name")
    path.with_name("example.hermes.yaml").write_text(
        yaml.safe_dump(
            {
                "delivery_defaults": {
                    "inputs": {
                        input_name: {"type": "string", "required": True},
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    item = _user_items(response)[0]
    assert item["inputs"] == []
    assert item["supported_inputs"] == {
        "supported": False,
        "reason": "unsupported_input_shape",
    }
    assert item["run_support"] == {
        "supported": False,
        "reason": "unsupported_inputs",
    }


def test_workflow_catalog_rejects_case_insensitive_input_name_collisions(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    path = workflow_writer(home / "workflows", name="colliding-input-names")
    path.with_name("example.hermes.yaml").write_text(
        yaml.safe_dump(
            {
                "delivery_defaults": {
                    "inputs": {
                        "Mode": {"type": "string", "required": True},
                        "mode": {"type": "string", "required": True},
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    item = _user_items(response)[0]
    assert item["supported_inputs"] == {
        "supported": False,
        "reason": "unsupported_input_shape",
    }


@pytest.mark.parametrize(
    "choices",
    [
        [1, 1.0],
        [9_007_199_254_740_992, 9_007_199_254_740_993],
    ],
)
def test_workflow_catalog_rejects_enum_choices_that_collapse_on_the_json_wire(
    tmp_path, monkeypatch, workflow_writer, choices
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    path = workflow_writer(home / "workflows", name="wire-unsafe-enum")
    path.with_name("example.hermes.yaml").write_text(
        yaml.safe_dump(
            {
                "delivery_defaults": {
                    "inputs": {
                        "mode": {
                            "type": "enum",
                            "required": True,
                            "values": choices,
                        }
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    item = _user_items(response)[0]
    assert item["inputs"] == [{"name": "mode", "type": "enum", "required": True}]
    assert item["supported_inputs"] == {
        "supported": False,
        "reason": "unsupported_input_shape",
    }


def test_workflow_catalog_degrades_unrepresentable_workflow_name_per_entry(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(home / "workflows", name="normal", filename="normal.yaml")
    workflow_writer(home / "workflows", name="placeholder", filename="long.yaml")
    import plugins.workflow.catalog_api as catalog_api

    original_compile = catalog_api.compile_workflow
    long_name = "x" * 129

    def long_name_compile(source, *args, **kwargs):
        compiled = original_compile(source, *args, **kwargs)
        if source.workflow_path.name == "long.yaml":
            return SimpleNamespace(
                package=replace(
                    compiled.package,
                    definition=replace(compiled.package.definition, name=long_name),
                )
            )
        return compiled

    monkeypatch.setattr(catalog_api, "compile_workflow", long_name_compile)

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    assert _user_items(response) == [
        {
            "name": "normal",
            "version": "1",
            "description": "Portable workflow fixture",
            "requires_ai": False,
            "source": "profile",
            "precedence": 2,
            "trust_state": "untrusted",
            "inputs": [],
            "supported_inputs": {
                "supported": True,
                "reason": "parameterless",
            },
            "run_support": {"supported": True, "reason": "supported"},
            "language": {
                "effective_profile": "hermes-legacy",
                "legacy": True,
            },
            "compatibility": {"level": "mapped", "runnable": True},
        },
        {"name": "x" * 128, "error": "invalid_definition"},
    ]


def test_workflow_catalog_empty_is_not_an_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    assert _user_items(response) == []
    assert response.json()["truncated"] is False


def test_workflow_catalog_isolates_invalid_definition(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(home / "workflows", name="valid", filename="valid.yaml")
    (home / "workflows" / "broken.yaml").write_text(
        "name: broken\nnodes: [SECRET_TRACEBACK_MATERIAL\n", encoding="utf-8"
    )

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    assert _user_items(response) == [
        {"name": "broken", "error": "invalid_definition"},
        {
            "name": "valid",
            "version": "1",
            "description": "Portable workflow fixture",
            "requires_ai": False,
            "source": "profile",
            "precedence": 2,
            "trust_state": "untrusted",
            "inputs": [],
            "supported_inputs": {
                "supported": True,
                "reason": "parameterless",
            },
            "run_support": {"supported": True, "reason": "supported"},
            "language": {
                "effective_profile": "hermes-legacy",
                "legacy": True,
            },
            "compatibility": {"level": "mapped", "runnable": True},
        },
    ]
    assert response.json()["truncated"] is False
    assert b"SECRET_TRACEBACK_MATERIAL" not in response.content
    assert b"Traceback" not in response.content


def test_workflow_catalog_caps_items_and_reports_truncation(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    for index in range(501):
        name = f"workflow-{index:03d}"
        workflow_writer(home / "workflows", name=name, filename=f"{name}.yaml")
    import plugins.workflow.catalog_api as catalog_api

    original_parse = catalog_api.parse_workflow_source_bytes
    original_read = catalog_api.WorkflowTrustStore._read
    loaded = 0
    trust_reads = 0

    def counted_parse(*args, **kwargs):
        nonlocal loaded
        loaded += 1
        return original_parse(*args, **kwargs)

    def counted_read(*args, **kwargs):
        nonlocal trust_reads
        trust_reads += 1
        return original_read(*args, **kwargs)

    monkeypatch.setattr(catalog_api, "parse_workflow_source_bytes", counted_parse)
    monkeypatch.setattr(catalog_api.WorkflowTrustStore, "_read", counted_read)

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    payload = response.json()
    user_items = _user_items(response)
    assert len(payload["items"]) == 500
    assert len(user_items) == 495
    assert payload["truncated"] is True
    assert user_items[0]["name"] == "workflow-000"
    assert user_items[-1]["name"] == "workflow-494"
    assert loaded == 500
    assert trust_reads == 1
    assert not (home / "workflow" / "trust.lock").exists()


def test_workflow_catalog_bounds_one_trust_snapshot_read(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(home / "workflows", name="bounded-trust")
    import plugins.workflow.catalog_api as catalog_api

    trust_path = WorkflowTrustStore(home).path
    trust_path.parent.mkdir(parents=True)
    trust_path.write_text(
        json.dumps({
            "version": 1,
            "records": {"padding": "x" * catalog_api.CATALOG_MAX_TRUST_STORE_BYTES},
        }),
        encoding="utf-8",
    )
    original_open = Path.open
    read_sizes: list[int] = []

    class RecordingReader:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __enter__(self):
            self._wrapped.__enter__()
            return self

        def __exit__(self, *args):
            return self._wrapped.__exit__(*args)

        def read(self, size=-1):
            read_sizes.append(size)
            return self._wrapped.read(size)

    def recording_open(path, *args, **kwargs):
        opened = original_open(path, *args, **kwargs)
        return RecordingReader(opened) if Path(path) == trust_path else opened

    monkeypatch.setattr(Path, "open", recording_open)

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    assert _user_items(response)[0]["trust_state"] == "untrusted"
    assert read_sizes == [catalog_api.CATALOG_MAX_TRUST_STORE_BYTES + 1]
    assert not WorkflowTrustStore(home).lock_path.exists()


def test_workflow_catalog_treats_non_object_trust_json_as_untrusted(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(home / "workflows", name="non-object-trust")
    trust_path = WorkflowTrustStore(home).path
    trust_path.parent.mkdir(parents=True)
    trust_path.write_text("[]", encoding="utf-8")

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    assert _user_items(response)[0]["trust_state"] == "untrusted"
    assert not WorkflowTrustStore(home).lock_path.exists()


def test_workflow_catalog_rejects_oversized_resource_without_reading_it_all(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(
        home / "workflows",
        name="oversized-resource",
        nodes=[{"id": "run", "command": "large"}],
    )
    commands = home / "commands"
    commands.mkdir()
    resource = commands / "large.md"
    resource.write_bytes(b"x" * (1024 * 1024 + 1))
    original_open = Path.open
    read_sizes: list[int] = []

    class RecordingReader:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __enter__(self):
            self._wrapped.__enter__()
            return self

        def __exit__(self, *args):
            return self._wrapped.__exit__(*args)

        def read(self, size=-1):
            read_sizes.append(size)
            return self._wrapped.read(size)

    def recording_open(path, *args, **kwargs):
        if Path(path) == resource:
            raise AssertionError("oversized resource was opened")
        opened = original_open(path, *args, **kwargs)
        return opened

    monkeypatch.setattr(Path, "open", recording_open)

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    assert _user_items(response) == [
        {"name": "oversized-resource", "error": "catalog_capacity"}
    ]
    assert read_sizes == []


def test_workflow_catalog_enforces_aggregate_resource_budget(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    commands = home / "commands"
    commands.mkdir(parents=True)
    nodes = []
    for index in range(9):
        name = f"resource-{index}"
        (commands / f"{name}.md").write_bytes(b"x" * 1024 * 1024)
        nodes.append({"id": f"node-{index}", "command": name})
    workflow_writer(
        home / "workflows",
        name="aggregate-resource-budget",
        nodes=nodes,
    )

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    assert _user_items(response) == [
        {"name": "aggregate-resource-budget", "error": "catalog_capacity"}
    ]


def test_workflow_catalog_truncates_before_global_resource_work_bound(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    import plugins.workflow.catalog_api as catalog_api

    monkeypatch.setattr(
        catalog_api,
        "CATALOG_MAX_RESOURCE_REQUEST_BYTES",
        catalog_api.CATALOG_MAX_RESOURCE_TOTAL_BYTES,
        raising=False,
    )
    workflow_writer(home / "workflows", name="alpha", filename="alpha.yaml")
    workflow_writer(home / "workflows", name="beta", filename="beta.yaml")

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    assert response.json()["truncated"] is True
    assert [item["name"] for item in _user_items(response)] == ["alpha"]


def test_workflow_catalog_stops_directory_enumeration_at_scan_budget(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    catalog = home / "workflows"
    catalog.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    import plugins.workflow.catalog_api as catalog_api

    monkeypatch.setattr(catalog_api, "CATALOG_MAX_SCAN_ENTRIES", 3, raising=False)
    enumerated = 0

    class FakeEntry:
        def __init__(self, index: int):
            self.name = f"ignored-{index}.txt"
            self.path = str(catalog / self.name)

        def is_dir(self, *, follow_symlinks=True):
            return False

        def is_file(self, *, follow_symlinks=True):
            return True

    def endless_entries(_directory):
        nonlocal enumerated
        while True:
            enumerated += 1
            yield FakeEntry(enumerated)

    monkeypatch.setattr(
        catalog_api, "_directory_entries", endless_entries, raising=False
    )

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 503
    assert response.json() == {
        "detail": {"code": "workflow_catalog_capacity", "retryable": True}
    }
    assert enumerated == 3


def test_workflow_catalog_maps_enumeration_failure_to_typed_unavailable(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    (home / "workflows").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    import plugins.workflow.catalog_api as catalog_api

    def fail_enumeration(_directory):
        raise PermissionError("SECRET_ENUMERATION_PATH")

    monkeypatch.setattr(
        catalog_api, "_directory_entries", fail_enumeration, raising=False
    )

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 503
    assert response.json() == {
        "detail": {"code": "workflow_catalog_unavailable", "retryable": True}
    }
    assert b"SECRET_ENUMERATION_PATH" not in response.content


def test_workflow_catalog_maps_trust_store_failure_to_typed_unavailable(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(home / "workflows", name="trust-failure")
    import plugins.workflow.catalog_api as catalog_api

    def fail_snapshot(*_args, **_kwargs):
        raise WorkflowTrustError("SECRET_TRUST_LOCK_PATH")

    monkeypatch.setattr(
        catalog_api.WorkflowTrustStore,
        "snapshot_read_only",
        fail_snapshot,
    )

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 503
    assert response.json() == {
        "detail": {"code": "workflow_trust_unavailable", "retryable": True}
    }
    assert b"SECRET_TRUST_LOCK_PATH" not in response.content


def test_workflow_catalog_enforces_definition_file_budget_per_entry(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    import plugins.workflow.catalog_api as catalog_api

    workflow_writer(home / "workflows", name="valid", filename="valid.yaml")
    oversized = home / "workflows" / "oversized.yaml"
    oversized.write_bytes(b" " * (catalog_api.CATALOG_MAX_DEFINITION_FILE_BYTES + 1))

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    items = _user_items(response)
    assert {item["name"] for item in items} == {"oversized", "valid"}
    assert next(item for item in items if item["name"] == "oversized") == {
        "name": "oversized",
        "error": "catalog_capacity",
    }
    assert "error" not in next(item for item in items if item["name"] == "valid")


def test_workflow_catalog_enforces_aggregate_definition_budget_per_entry(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    import plugins.workflow.catalog_api as catalog_api

    alpha = workflow_writer(home / "workflows", name="alpha", filename="alpha.yaml")
    bravo = workflow_writer(home / "workflows", name="bravo", filename="bravo.yaml")
    monkeypatch.setattr(
        catalog_api,
        "CATALOG_MAX_DEFINITION_TOTAL_BYTES",
        alpha.stat().st_size + bravo.stat().st_size - 1,
    )

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    items = _user_items(response)
    assert "error" not in next(item for item in items if item["name"] == "alpha")
    assert next(item for item in items if item["name"] == "bravo") == {
        "name": "bravo",
        "error": "catalog_capacity",
    }


def test_workflow_catalog_classifies_projection_exhaustion_as_capacity(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(
        home / "workflows",
        name="projection-capacity",
        nodes=[
            {"id": f"node-{index:03d}", "bash": "true"}
            for index in range(513)
        ],
    )

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    assert _user_items(response) == [
        {"name": "projection-capacity", "error": "catalog_capacity"}
    ]


def test_workflow_catalog_project_definition_overrides_profile(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "repo"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(
        home / "workflows",
        name="shared",
        filename="profile.yaml",
        description="profile definition",
    )
    workflow_writer(
        workdir / ".hermes" / "workflows",
        name="shared",
        filename="project.yaml",
        description="project definition",
    )

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    assert _user_items(response) == [
        {
            "name": "shared",
            "version": "1",
            "description": "project definition",
            "requires_ai": False,
            "source": "project",
            "precedence": 1,
            "trust_state": "untrusted",
            "inputs": [],
            "supported_inputs": {
                "supported": True,
                "reason": "parameterless",
            },
            "run_support": {"supported": True, "reason": "supported"},
            "language": {
                "effective_profile": "hermes-legacy",
                "legacy": True,
            },
            "compatibility": {"level": "mapped", "runnable": True},
        }
    ]


def test_workflow_catalog_isolates_same_precedence_duplicate_names(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(home / "workflows" / "one", name="duplicate")
    workflow_writer(home / "workflows" / "two", name="duplicate")
    workflow_writer(home / "workflows", name="valid", filename="valid.yaml")

    response = _catalog_get(_module().router, token=_reader())

    assert response.status_code == 200
    items = _user_items(response)
    assert items[0] == {"name": "duplicate", "error": "invalid_definition"}
    assert items[1]["name"] == "valid"
    assert "error" not in items[1]
