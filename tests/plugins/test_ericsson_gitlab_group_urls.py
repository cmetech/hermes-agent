"""Regression tests for standards-compliant GitLab group URLs."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest


def _load_connector_modules():
    plugin_root = Path(__file__).resolve().parents[2] / "plugins" / "ericsson-gitlab"
    package_name = "ericsson_gitlab_group_url_test"
    if package_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            package_name,
            plugin_root / "__init__.py",
            submodule_search_locations=[str(plugin_root)],
        )
        assert spec is not None and spec.loader is not None
        package = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = package
        spec.loader.exec_module(package)
    return (
        importlib.import_module(f"{package_name}.client"),
        importlib.import_module(f"{package_name}.models"),
        importlib.import_module(f"{package_name}.operations"),
    )


def _client_with_transport(client_module, models_module, handler):
    authentication = models_module.GitLabAuth(
        origin="https://gitlab.example.test",
        pat="test-token",
    )
    client = client_module.GitLabClient(authentication, max_retries=0)
    client._client.close()
    client._client = httpx.Client(
        base_url=authentication.origin,
        headers={"PRIVATE-TOKEN": authentication.pat, "Accept": "application/json"},
        transport=httpx.MockTransport(handler),
    )
    return client


def test_group_listing_accepts_gitlabs_canonical_groups_web_url() -> None:
    """A normal ``/groups/<path>`` API response must not become invalid data."""
    client_module, models_module, operations_module = _load_connector_modules()

    def gitlab(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v4/groups/example":
            return httpx.Response(
                200,
                json={
                    "id": 42,
                    "name": "Example",
                    "full_path": "example",
                    "parent_id": None,
                    "web_url": "https://gitlab.example.test/groups/example",
                },
            )
        if request.url.path == "/api/v4/groups/42/projects":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": "404 Not Found"})

    client = _client_with_transport(client_module, models_module, gitlab)
    try:
        result = operations_module.GitLabOperations(client).list_group_projects(
            "example",
            recursive=False,
        )
    finally:
        client.close()

    assert result["root_group"] == {
        "id": 42,
        "name": "Example",
        "full_path": "example",
        "parent_id": None,
        "web_url": "https://gitlab.example.test/groups/example",
        "project_count": 0,
    }
    assert result["groups"] == [result["root_group"]]
    assert result["projects"] == []
    assert result["complete"] is True


def test_group_listing_accepts_canonical_group_url_as_input() -> None:
    """A copied GitLab group URL must resolve the group path after ``/groups``."""
    client_module, models_module, operations_module = _load_connector_modules()

    def gitlab(request: httpx.Request) -> httpx.Response:
        raw_path = request.url.raw_path.split(b"?", 1)[0]
        if raw_path == b"/api/v4/groups/parent%2Fchild":
            return httpx.Response(
                200,
                json={
                    "id": 43,
                    "name": "Child",
                    "full_path": "parent/child",
                    "parent_id": 42,
                    "web_url": "https://gitlab.example.test/groups/parent/child",
                },
            )
        if request.url.path == "/api/v4/groups/43/projects":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": "404 Not Found"})

    client = _client_with_transport(client_module, models_module, gitlab)
    try:
        result = operations_module.GitLabOperations(client).list_group_projects(
            "https://gitlab.example.test/groups/parent/child",
            recursive=False,
        )
    finally:
        client.close()

    assert result["root_group"]["id"] == 43
    assert result["root_group"]["full_path"] == "parent/child"
    assert result["root_group"]["web_url"] == (
        "https://gitlab.example.test/groups/parent/child"
    )


def test_recursive_group_listing_normalizes_descendants_and_projects() -> None:
    """Recursive exploration must accept canonical URLs throughout the result."""
    client_module, models_module, operations_module = _load_connector_modules()

    def gitlab(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v4/groups/example":
            return httpx.Response(
                200,
                json={
                    "id": 42,
                    "name": "Example",
                    "full_path": "example",
                    "parent_id": None,
                    "web_url": "https://gitlab.example.test/groups/example",
                },
            )
        if request.url.path == "/api/v4/groups/42/descendant_groups":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 43,
                        "name": "Child",
                        "full_path": "example/child",
                        "parent_id": 42,
                        "web_url": ("https://gitlab.example.test/groups/example/child"),
                    }
                ],
            )
        if request.url.path == "/api/v4/groups/42/projects":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 99,
                        "name": "Repository",
                        "path_with_namespace": "example/child/repository",
                        "namespace": {
                            "kind": "group",
                            "full_path": "example/child",
                        },
                        "archived": False,
                        "default_branch": "main",
                        "web_url": (
                            "https://gitlab.example.test/example/child/repository"
                        ),
                    }
                ],
            )
        return httpx.Response(404, json={"message": "404 Not Found"})

    client = _client_with_transport(client_module, models_module, gitlab)
    try:
        result = operations_module.GitLabOperations(client).list_group_projects(
            "example",
            recursive=True,
        )
    finally:
        client.close()

    assert [group["full_path"] for group in result["groups"]] == [
        "example",
        "example/child",
    ]
    assert result["groups"][1]["web_url"] == (
        "https://gitlab.example.test/groups/example/child"
    )
    assert result["groups"][1]["project_count"] == 1
    assert [project["path_with_namespace"] for project in result["projects"]] == [
        "example/child/repository"
    ]
    assert result["group_count"] == 2
    assert result["project_count"] == 1
    assert result["complete"] is True


def test_recursive_group_listing_uses_supported_descendant_ordering() -> None:
    """Recursive exploration must not send GitLab an unsupported order field."""
    client_module, models_module, operations_module = _load_connector_modules()

    def gitlab(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v4/groups/sd-macs-att-rnam-hosting":
            return httpx.Response(
                200,
                json={
                    "id": 42,
                    "name": "RNAM Hosting",
                    "full_path": "sd-macs-att-rnam-hosting",
                    "parent_id": None,
                    "web_url": (
                        "https://gitlab.example.test/groups/"
                        "sd-macs-att-rnam-hosting"
                    ),
                },
            )
        if request.url.path == "/api/v4/groups/42/descendant_groups":
            if request.url.params.get("order_by") not in {"name", "path", "id"}:
                return httpx.Response(400, json={"message": "order_by is invalid"})
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v4/groups/42/projects":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": "404 Not Found"})

    client = _client_with_transport(client_module, models_module, gitlab)
    try:
        result = operations_module.GitLabOperations(client).list_group_projects(
            "sd-macs-att-rnam-hosting",
            recursive=True,
            max_groups=50,
            max_projects=100,
        )
    finally:
        client.close()

    assert result["root_group"]["full_path"] == "sd-macs-att-rnam-hosting"
    assert result["groups"] == [result["root_group"]]
    assert result["projects"] == []
    assert result["complete"] is True


def test_group_listing_still_rejects_cross_origin_group_urls() -> None:
    """Canonical path handling must not permit a remote response origin change."""
    client_module, models_module, operations_module = _load_connector_modules()

    def gitlab(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v4/groups/example":
            return httpx.Response(
                200,
                json={
                    "id": 42,
                    "name": "Example",
                    "full_path": "example",
                    "parent_id": None,
                    "web_url": "https://attacker.example/groups/example",
                },
            )
        return httpx.Response(404, json={"message": "404 Not Found"})

    client = _client_with_transport(client_module, models_module, gitlab)
    try:
        with pytest.raises(models_module.GitLabError) as raised:
            operations_module.GitLabOperations(client).list_group_projects(
                "example",
                recursive=False,
            )
    finally:
        client.close()

    assert raised.value.category == "invalid_remote_data"
