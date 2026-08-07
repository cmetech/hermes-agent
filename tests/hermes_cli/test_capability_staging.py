"""Capability-staging seam: resolver + manifest-driven staging (P2b)."""
import json
import subprocess
from pathlib import Path

import pytest

from hermes_cli import capability_staging as cs
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.schema import load_workflow, parse_workflow_source_bytes
from plugins.workflow.trust import (
    WorkflowTrustStore,
    compute_package_digest,
)


def _write(p: Path, text: str = "x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def make_bundle(root: Path, version: str = "0.2.0", requires_env=None) -> Path:
    """A minimal on-disk capability bundle matching the ericsson repo layout."""
    b = root / "bundle"
    manifest = {
        "name": "ericsson", "version": version, "description": "t",
        "requiresEnv": requires_env if requires_env is not None else {},
        "disabledByDefault": {"skills": [], "toolsets": ["ericsson-jira"]},
        "skills": [],
        "plugins": ["plugins/workflow", "plugins/ericsson-jira"],
        "mcpServers": "mcp/mcp-servers.yaml",
        "mcpLocal": ["mcp/outlook-mcp"],
        "workflowPackages": [{
            "path": "capabilities/workflow-packages/ericsson",
            "digestManifest": "capabilities/workflow-packages/ericsson/digests.json",
        }],
        "personas": [], "env": [],
    }
    _write(b / "sets/ericsson.json", json.dumps(manifest))
    _write(b / "plugins/ericsson-jira/plugin.yaml", "name: ericsson-jira\n")
    _write(b / "plugins/ericsson-jira/__init__.py", "")
    _write(b / "mcp/outlook-mcp/run_server.py", "# server")
    _write(b / "mcp/mcp-servers.yaml",
           "mcp_servers:\n"
           "  outlook:\n"
           "    command: python\n"
           "    args: [\"${CAPABILITY_DIR}/outlook-mcp/run_server.py\"]\n"
           "  glean:\n"
           "    enabled: false\n"
           "    url: https://default.example.test/mcp\n"
           "    headers:\n"
           "      Authorization: \"Bearer ${GLEAN_API_TOKEN}\"\n")
    package = b / "capabilities/workflow-packages/ericsson"
    workflow = _write(
        package / "workflows/my-tickets-summary.yaml",
        "name: my-tickets-summary\n"
        "description: Portable ticket summary\n"
        "nodes:\n"
        "  - id: collect\n"
        "    command: collect\n",
    )
    _write(package / "commands/collect.md", "Collect fictional tickets.\n")
    digest = compute_package_digest(load_workflow(workflow)).sha256
    _write(
        package / "digests.json",
        json.dumps({
            "schemaVersion": 1,
            "packages": {"my-tickets-summary": digest},
        }),
    )
    return b


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def fake_config(monkeypatch):
    """In-memory config.yaml stand-in for load_config/save_config."""
    store = {"config": {}}
    saved = {"count": 0}

    def load_config():
        return json.loads(json.dumps(store["config"]))

    def save_config(cfg, **kw):
        store["config"] = cfg
        saved["count"] += 1

    import hermes_cli.config as config_mod
    monkeypatch.setattr(config_mod, "load_config", load_config)
    monkeypatch.setattr(config_mod, "save_config", save_config)
    return store, saved


def test_resolver_env_override(tmp_path, home, monkeypatch):
    b = make_bundle(tmp_path)
    monkeypatch.setenv("OTTO_CAPABILITY_SOURCE", str(b))
    got = cs.resolve_capability_bundle("ericsson", "https://example/nope.git", home)
    assert got == b


def test_resolver_env_override_wrong_set(tmp_path, home, monkeypatch):
    monkeypatch.setenv("OTTO_CAPABILITY_SOURCE", str(tmp_path))  # no sets/ericsson.json
    assert cs.resolve_capability_bundle("ericsson", "https://example/e.git", home) is None


def test_resolver_git_clone_and_cache(tmp_path, home, monkeypatch):
    monkeypatch.delenv("OTTO_CAPABILITY_SOURCE", raising=False)
    src = make_bundle(tmp_path)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=src, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "-C", str(src), "add", "-A"], check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "-C", str(src), "commit", "-qm", "init"], check=True)
    got = cs.resolve_capability_bundle("ericsson", str(src), home)  # local path URL
    assert got is not None and (got / "sets/ericsson.json").exists()
    assert str(got).startswith(str(home))          # cached under home
    # second resolve reuses the cache (pull path) and still succeeds
    again = cs.resolve_capability_bundle("ericsson", str(src), home)
    assert again == got


def test_resolver_bad_url_no_cache(home, monkeypatch):
    monkeypatch.delenv("OTTO_CAPABILITY_SOURCE", raising=False)
    assert cs.resolve_capability_bundle("ericsson", "/nonexistent/repo.git", home) is None


def test_stage_bundle_full(tmp_path, home, fake_config):
    store, saved = fake_config
    b = make_bundle(tmp_path)
    changed = cs.stage_bundle(b, "ericsson", home)
    assert changed is True
    assert not (home / "plugins/workflow").exists()
    assert (home / "plugins/ericsson-jira/plugin.yaml").exists()
    assert (home / "plugins/outlook-mcp/run_server.py").exists()
    package = home / "workflows/ericsson"
    assert (package / "workflows/my-tickets-summary.yaml").exists()
    assert (package / "commands/collect.md").exists()
    assert (package / "digests.json").exists()
    cfg = store["config"]
    outlook = cfg["mcp_servers"]["outlook"]
    assert outlook["args"][0] == str(home / "plugins") + "/outlook-mcp/run_server.py"
    assert "ericsson-jira" in cfg["disabled_toolsets"]
    assert "workflow" in cfg["plugins"]["enabled"]
    assert "ericsson-jira" in cfg["plugins"]["enabled"]
    assert cfg["plugins"]["entries"]["workflow"]["agent"] == {
        "allow_model_override": True,
        "allow_provider_override": True,
    }


def test_stage_bundle_preserves_explicit_workflow_agent_override_denials(
    tmp_path, home, fake_config
):
    store, _ = fake_config
    store["config"] = {
        "plugins": {
            "entries": {
                "workflow": {
                    "agent": {
                        "allow_model_override": False,
                        "allow_provider_override": False,
                    }
                }
            }
        }
    }

    cs.stage_bundle(make_bundle(tmp_path), "ericsson", home)

    assert store["config"]["plugins"]["entries"]["workflow"]["agent"] == {
        "allow_model_override": False,
        "allow_provider_override": False,
    }


def test_stage_bundle_never_clobbers_user_mcp(tmp_path, home, fake_config):
    store, _ = fake_config
    store["config"] = {"mcp_servers": {"outlook": {"command": "custom"}}}
    b = make_bundle(tmp_path)
    cs.stage_bundle(b, "ericsson", home)
    assert store["config"]["mcp_servers"]["outlook"] == {"command": "custom"}


def test_stage_bundle_backfills_only_blank_mcp_url(tmp_path, home, fake_config):
    store, _ = fake_config
    store["config"] = {
        "mcp_servers": {
            "glean": {
                "enabled": True,
                "url": "   ",
                "headers": {"X-User": "preserve"},
            }
        }
    }
    b = make_bundle(tmp_path)

    cs.stage_bundle(b, "ericsson", home)

    assert store["config"]["mcp_servers"]["glean"] == {
        "enabled": True,
        "url": "https://default.example.test/mcp",
        "headers": {"X-User": "preserve"},
    }


def test_stage_bundle_preserves_custom_mcp_url(tmp_path, home, fake_config):
    store, _ = fake_config
    glean = {
        "url": "https://custom.example.test/mcp",
        "headers": {"X-User": "preserve"},
    }
    store["config"] = {"mcp_servers": {"glean": glean}}
    before = json.loads(json.dumps(glean))
    b = make_bundle(tmp_path)

    cs.stage_bundle(b, "ericsson", home)

    assert store["config"]["mcp_servers"]["glean"] == before


def test_stage_brand_capabilities_end_to_end(tmp_path, home, fake_config, monkeypatch):
    b = make_bundle(tmp_path, requires_env={"ERICSSON_ENV": "1"})
    monkeypatch.setenv("OTTO_CAPABILITY_SOURCE", str(b))
    monkeypatch.setenv("ERICSSON_ENV", "1")
    fake_root = tmp_path / "fakeroot"
    _write(fake_root / "brands/otto.json", json.dumps({
        "slug": "otto",
        "capabilitySets": ["ericsson"],
        "capabilitySources": {"ericsson": "https://example/e.git"},
    }))
    _write(fake_root / "brand/active", "otto")
    cs.stage_brand_capabilities(home, root=fake_root)
    assert (home / "workflows/ericsson/workflows/my-tickets-summary.yaml").exists()
    stamp = json.loads((home / cs.STAGING_MANIFEST).read_text())
    assert stamp["sets"]["ericsson"]["version"] == "0.2.0"
    # idempotent second run: nothing re-copied (marker: delete a staged file; version match -> skip)
    staged_workflow = home / "workflows/ericsson/workflows/my-tickets-summary.yaml"
    staged_workflow.unlink()
    cs.stage_brand_capabilities(home, root=fake_root)
    assert not staged_workflow.exists()
    # version bump -> re-stages
    m = json.loads((b / "sets/ericsson.json").read_text())
    m["version"] = "0.3.0"
    (b / "sets/ericsson.json").write_text(json.dumps(m))
    cs.stage_brand_capabilities(home, root=fake_root)
    assert staged_workflow.exists()


def test_complete_package_swap_is_atomic_and_distribution_trust_is_digest_bound(
    tmp_path, home, fake_config
):
    bundle = make_bundle(tmp_path)
    old = home / "workflows/ericsson"
    _write(old / "workflows/old.yaml", "old package remains usable\n")

    with pytest.raises(RuntimeError, match="before package swap"):
        cs.stage_bundle(
            bundle,
            "ericsson",
            home,
            trusted_distribution=True,
            fault_injector=lambda phase, _path: (
                (_ for _ in ()).throw(RuntimeError("before package swap"))
                if phase == "before-workflow-package-swap"
                else None
            ),
        )

    assert (old / "workflows/old.yaml").read_text() == "old package remains usable\n"
    assert not list((home / "workflows").glob(".ericsson-stage-*"))

    cs.stage_bundle(bundle, "ericsson", home, trusted_distribution=True)
    workflow = old / "workflows/my-tickets-summary.yaml"
    package = load_workflow(workflow)
    digest = compute_package_digest(package).sha256
    assert WorkflowTrustStore(home).check(digest) == "trusted"
    trust = json.loads((home / "workflow/trust.json").read_text())
    assert trust["records"][digest]["actor"] == "trusted_distribution"


def test_trusted_distribution_stages_and_trusts_phase4_composite_digest(
    tmp_path,
    home,
    fake_config,
) -> None:
    bundle = make_bundle(tmp_path)
    package_root = bundle / "capabilities/workflow-packages/ericsson"
    workflow = package_root / "workflows/my-tickets-summary.yaml"
    sidecar = workflow.with_name("my-tickets-summary.hermes.yaml")
    sidecar.write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    source = parse_workflow_source_bytes(
        workflow,
        workflow_bytes=workflow.read_bytes(),
        sidecar_bytes=sidecar.read_bytes(),
        source="distribution",
        precedence=1,
    )
    compilation = compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=4,
    )
    (package_root / "digests.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "packages": {
                "my-tickets-summary": compilation.composite_digest,
            },
        }),
        encoding="utf-8",
    )

    cs.stage_bundle(bundle, "ericsson", home, trusted_distribution=True)

    trust = WorkflowTrustStore(home)
    assert trust.check(compilation.composite_digest) == "trusted"
    assert trust.check(compute_package_digest(compilation.package).sha256) == (
        "untrusted"
    )


def test_package_digest_mismatch_preserves_previous_package_and_trust(
    tmp_path, home, fake_config
):
    bundle = make_bundle(tmp_path)
    cs.stage_bundle(bundle, "ericsson", home, trusted_distribution=True)
    destination = home / "workflows/ericsson"
    before = (destination / "workflows/my-tickets-summary.yaml").read_bytes()
    trust_before = (home / "workflow/trust.json").read_bytes()
    (bundle / "capabilities/workflow-packages/ericsson/commands/collect.md").write_text(
        "tampered\n"
    )

    with pytest.raises(ValueError, match="digest mismatch"):
        cs.stage_bundle(bundle, "ericsson", home, trusted_distribution=True)

    assert (destination / "workflows/my-tickets-summary.yaml").read_bytes() == before
    assert (home / "workflow/trust.json").read_bytes() == trust_before


def test_direct_user_staging_cannot_claim_distribution_trust(
    tmp_path, home, fake_config
):
    bundle = make_bundle(tmp_path)

    cs.stage_bundle(bundle, "ericsson", home)

    package = load_workflow(
        home / "workflows/ericsson/workflows/my-tickets-summary.yaml"
    )
    digest = compute_package_digest(package).sha256
    assert WorkflowTrustStore(home).check(digest) == "untrusted"


def test_requires_env_gate(tmp_path, home, fake_config, monkeypatch):
    b = make_bundle(tmp_path, requires_env={"ERICSSON_ENV": "1"})
    monkeypatch.setenv("OTTO_CAPABILITY_SOURCE", str(b))
    monkeypatch.delenv("ERICSSON_ENV", raising=False)
    fake_root = tmp_path / "fakeroot"
    _write(fake_root / "brands/otto.json", json.dumps({
        "slug": "otto", "capabilitySets": ["ericsson"],
        "capabilitySources": {"ericsson": "u"},
    }))
    _write(fake_root / "brand/active", "otto")
    cs.stage_brand_capabilities(home, root=fake_root)
    assert not (home / "skills").exists()          # gated: nothing staged


def test_empty_sets_still_noop(home, tmp_path, monkeypatch):
    fake_root = tmp_path / "fakeroot"
    _write(fake_root / "brands/otto.json", json.dumps({"slug": "otto", "capabilitySets": []}))
    _write(fake_root / "brand/active", "otto")
    cs.stage_brand_capabilities(home, root=fake_root)   # must not raise, must not create anything
    assert list(home.iterdir()) == []


def test_resolve_placeholders_survives_windows_backslashes():
    replacement = r"C:\Users\t\.otto\plugins"
    obj = {
        "args": ["${CAPABILITY_DIR}/x", "--flag"],
        "nested": {"path": "${CAPABILITY_DIR}/y"},
        "untouched": 5,
    }
    resolved = cs._resolve_placeholders(obj, replacement)
    assert resolved["args"][0] == replacement + "/x"
    assert resolved["nested"]["path"] == replacement + "/y"
    assert resolved["untouched"] == 5


def test_stage_bundle_rejects_path_traversal(tmp_path, home, fake_config):
    b = make_bundle(tmp_path)
    manifest_path = b / "sets/ericsson.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["skills"] = ["skills/../../evil"]
    manifest_path.write_text(json.dumps(manifest))
    # a real dir at the traversal target (outside the bundle) proves the guard
    # skips the entry rather than merely failing to find a source dir
    _write(tmp_path / "evil" / "marker.txt", "should not be copied")
    cs.stage_bundle(b, "ericsson", home)
    assert not (home / "evil").exists()
    # the guard must skip the entry before any copy is attempted: the traversal
    # source dir should be untouched (still just its original marker file)
    assert [p.name for p in (tmp_path / "evil").iterdir()] == ["marker.txt"]


def test_stage_bundle_rejects_absolute_path(tmp_path, home, fake_config):
    b = make_bundle(tmp_path)
    manifest_path = b / "sets/ericsson.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["skills"] = ["/etc"]
    manifest_path.write_text(json.dumps(manifest))
    cs.stage_bundle(b, "ericsson", home)
    assert not (home / "skills" / "etc").exists()
    assert not (home / "etc").exists()


def test_stage_bundle_rejects_workflow_package_traversal(
    tmp_path, home, fake_config
):
    bundle = make_bundle(tmp_path)
    manifest_path = bundle / "sets/ericsson.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["workflowPackages"][0]["path"] = "../outside"
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="workflow package path"):
        cs.stage_bundle(bundle, "ericsson", home)

    assert not (home / "workflows/ericsson").exists()


def test_stage_bundle_rejects_symlink_inside_workflow_package(
    tmp_path, home, fake_config
):
    bundle = make_bundle(tmp_path)
    package = bundle / "capabilities/workflow-packages/ericsson"
    outside = _write(tmp_path / "outside.md", "outside\n")
    link = package / "commands/escape.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ValueError, match="symlink"):
        cs.stage_bundle(bundle, "ericsson", home)

    assert not (home / "workflows/ericsson").exists()


def test_stage_bundle_config_targets_staged_home(tmp_path, monkeypatch):
    """stage_bundle's config round-trip must target the `home` PARAMETER, never
    the ambient process home (HERMES_HOME env / context override / platform
    default). Deliberately does NOT use the fake_config fixture: it exercises
    the real load_config()/save_config() to prove the context-override fix
    actually redirects config IO, not just a stubbed call.
    """
    other_home = tmp_path / "other"; other_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(other_home))   # ambient process home elsewhere
    staged_home = tmp_path / "staged"; staged_home.mkdir()
    b = make_bundle(tmp_path)
    cs.stage_bundle(b, "ericsson", staged_home)
    assert (staged_home / "config.yaml").exists()
    text = (staged_home / "config.yaml").read_text()
    assert "ericsson-jira" in text and "outlook" in text
    assert not (other_home / "config.yaml").exists() or "ericsson" not in (other_home / "config.yaml").read_text()
