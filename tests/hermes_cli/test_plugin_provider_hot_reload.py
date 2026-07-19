from __future__ import annotations

import json
from pathlib import Path
import textwrap
from types import SimpleNamespace

import pytest

from agent.image_gen_registry import (
    _reset_for_tests,
    list_providers,
    register_provider,
)
from hermes_cli.config import load_config, save_config
from hermes_cli.plugins import PluginManager
import hermes_cli.web_server as web_server
from tools.image_generation_tool import _dispatch_to_plugin_provider


def _write_plugin(root: Path, relative: str, module: str) -> None:
    directory = root / relative
    directory.mkdir(parents=True)
    (directory / "plugin.yaml").write_text(
        "name: " + directory.name + "\nversion: 1.0.0\nkind: backend\n",
        encoding="utf-8",
    )
    (directory / "__init__.py").write_text(
        textwrap.dedent(module), encoding="utf-8"
    )


def test_hosted_missing_provider_does_not_force_tool_rediscovery(
    monkeypatch,
) -> None:
    from agent import image_gen_registry
    from hermes_cli import plugins as plugins_module
    from tools import image_generation_tool

    manager = SimpleNamespace(has_bound_background_service_host=True)
    calls: list[bool] = []

    def ensure(force: bool = False):
        calls.append(force)
        return manager

    monkeypatch.setattr(plugins_module, "_ensure_plugins_discovered", ensure)
    monkeypatch.setattr(image_gen_registry, "get_provider", lambda _name: None)
    monkeypatch.setattr(
        image_generation_tool,
        "_read_configured_image_provider",
        lambda: "not-yet-published",
    )

    payload = json.loads(
        image_generation_tool._dispatch_to_plugin_provider("hello", "square")
    )

    assert calls == [False]
    assert payload["error_type"] == "provider_not_registered"


@pytest.mark.asyncio
async def test_provider_hot_add_uses_production_web_host_reload(
    tmp_path, monkeypatch
) -> None:
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    _write_plugin(
        bundled,
        "lifecycle",
        """
        from hermes_cli.plugin_services import BackgroundServiceHealth

        class Service:
            def run(self, stop_event):
                stop_event.wait()

            def health(self):
                return BackgroundServiceHealth(state="healthy", code="ready")

        def register(ctx):
            ctx.register_background_service(
                "service", lambda _context: Service(), hosts={"web"}
            )
        """,
    )
    manager = PluginManager()
    monkeypatch.setattr("hermes_cli.plugins.get_bundled_plugins_dir", lambda: bundled)
    monkeypatch.setattr("hermes_cli.plugins._plugin_manager", manager)
    prior_providers = list_providers()
    _reset_for_tests()
    manager.discover_and_load()
    old_host = manager.start_background_services("web")
    app = SimpleNamespace(
        state=SimpleNamespace(plugin_background_services=old_host)
    )

    _write_plugin(
        bundled,
        "image_gen/hot-add",
        """
        from agent.image_gen_provider import ImageGenProvider

        class HotAddProvider(ImageGenProvider):
            @property
            def name(self):
                return "hot-add"

            def generate(self, prompt, aspect_ratio="landscape", **kwargs):
                return {
                    "success": True,
                    "image": "memory://hot-add",
                    "provider": self.name,
                    "prompt": prompt,
                    "aspect_ratio": aspect_ratio,
                }

        def register(ctx):
            ctx.register_image_gen_provider(HotAddProvider())
        """,
    )
    config = load_config()
    config["image_gen"] = {"provider": "hot-add"}
    save_config(config)

    try:
        result = await web_server._reload_plugin_background_services(
            app, timeout=1.0
        )
        dispatched = _dispatch_to_plugin_provider("hello", "square")
        payload = json.loads(dispatched)

        assert result == {"ok": True}
        assert app.state.plugin_background_services is not old_host
        assert payload["success"] is True
        assert payload["provider"] == "hot-add"
        assert payload["image"] == "memory://hot-add"
    finally:
        host = app.state.plugin_background_services
        if host is not old_host:
            assert host.shutdown(timeout=1)
        elif not old_host.is_quiescent:
            assert old_host.shutdown(timeout=1)
        _reset_for_tests()
        for provider in prior_providers:
            register_provider(provider)
