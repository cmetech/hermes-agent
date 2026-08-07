from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from plugins.workflow import schema as schema_module
from plugins.workflow.models import WorkflowNodeOrigin, WorkflowValidationError


def _source_bytes(*, name: str = "child", iterations: int = 2) -> bytes:
    return (
        f"name: {name}\n"
        "description: Child\n"
        "nodes:\n"
        "  - id: x\n"
        "    loop:\n"
        "      prompt: hi\n"
        "      until: DONE\n"
        f"      max_iterations: {iterations}\n"
    ).encode()


def test_source_parser_retains_authenticated_bytes_lines_and_inactive_sidecar(
    tmp_path,
) -> None:
    """Catch source parsing that normalizes away bytes or child policy metadata."""
    workflow_path = tmp_path / "catalog" / "child.yaml"
    workflow_bytes = _source_bytes()
    sidecar_bytes = b"language_compatibility: hermes-legacy\n"

    source = schema_module.parse_workflow_source_bytes(
        workflow_path,
        workflow_bytes=workflow_bytes,
        sidecar_bytes=sidecar_bytes,
        source="project",
        precedence=1,
    )

    assert source.name == "child"
    assert source.sidecar["language_compatibility"] == "hermes-legacy"
    assert source.nodes[0].source_line == 4
    assert source.definition_bytes == workflow_bytes
    assert source.sidecar_bytes == sidecar_bytes
    assert source.definition_location == "child.yaml"
    assert source.sidecar_location == "child.hermes.yaml"
    with pytest.raises(TypeError):
        source.nodes[0].value["prompt"] = "changed"
    with pytest.raises(FrozenInstanceError):
        source.name = "changed"


@pytest.mark.parametrize("iterations", (1, 100))
def test_source_parser_accepts_exact_loop_integer_boundaries(
    tmp_path, iterations
) -> None:
    """Catch lossy/coercive source parsing at admitted integer boundaries."""
    source = schema_module.parse_workflow_source_bytes(
        tmp_path / "bounded.yaml",
        workflow_bytes=_source_bytes(iterations=iterations),
        sidecar_bytes=None,
        source="explicit",
        precedence=0,
    )

    assert source.nodes[0].value["max_iterations"] == iterations
    assert isinstance(source.nodes[0].value["max_iterations"], int)


@pytest.mark.parametrize("iterations", (0, 101))
def test_source_parser_rejects_loop_integers_outside_exact_bounds(
    tmp_path, iterations
) -> None:
    """Catch raw sources deferring bounded integer validation until execution."""
    with pytest.raises(WorkflowValidationError) as exc:
        schema_module.parse_workflow_source_bytes(
            tmp_path / "bounded.yaml",
            workflow_bytes=_source_bytes(iterations=iterations),
            sidecar_bytes=None,
            source="explicit",
            precedence=0,
        )

    assert exc.value.issues[0].code == "invalid_loop"


@pytest.mark.parametrize(
    ("workflow_bytes", "code"),
    [
        (
            b"name: unsafe\ndescription: Unsafe\nnodes: !!python/object/apply:os.system ['id']\n",
            "invalid_yaml",
        ),
        (
            b"name: trusted\ndescription: Trusted\ntrust: true\nnodes:\n  - id: x\n    prompt: hi\n",
            "self_trust",
        ),
    ],
)
def test_source_parser_rejects_unsafe_yaml_and_self_trust(
    tmp_path, workflow_bytes, code
) -> None:
    """Catch source capture bypassing the existing parser security boundary."""
    with pytest.raises(WorkflowValidationError) as exc:
        schema_module.parse_workflow_source_bytes(
            tmp_path / "unsafe.yaml",
            workflow_bytes=workflow_bytes,
            sidecar_bytes=None,
            source="project",
            precedence=1,
        )

    assert exc.value.issues[0].code == code


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"definition_location": "/private/child.yaml"}, "logical location"),
        ({"sidecar_location": "../child.hermes.yaml"}, "logical location"),
        ({"source": "s" * 129}, "catalog source"),
        ({"precedence": True}, "precedence"),
    ],
)
def test_source_document_rejects_absolute_locations_and_unbounded_metadata(
    tmp_path, changes, message
) -> None:
    """Catch public source identity retaining paths or unbounded catalog metadata."""
    source = schema_module.parse_workflow_source_bytes(
        tmp_path / "child.yaml",
        workflow_bytes=_source_bytes(),
        sidecar_bytes=b"language_compatibility: hermes-legacy\n",
        source="project",
        precedence=1,
    )

    with pytest.raises(ValueError, match=message):
        replace(source, **changes)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"definition_location": "/private/child.yaml"}, "logical location"),
        ({"include_instance_path": ("a", "b", "c", "d")}, "include depth"),
        ({"package_key": "p" * 4097}, "package_key"),
        ({"precedence": -1}, "precedence"),
    ],
)
def test_node_origin_rejects_absolute_locations_and_unbounded_metadata(
    changes, message
) -> None:
    """Catch node provenance leaking paths or admitting unbounded identifiers."""
    values = {
        "include_instance_path": ("checks",),
        "package_key": "project:child",
        "workflow_name": "child",
        "catalog_source": "project",
        "precedence": 1,
        "definition_location": "workflows/child.yaml",
        "source_index": 0,
        "source_line": 4,
        "expanded_node_id": "checks__x",
    }
    values.update(changes)

    with pytest.raises(ValueError, match=message):
        WorkflowNodeOrigin(**values)


def _parse_source(path, *, name: str, source: str, precedence: int, sidecar=None):
    workflow_bytes = _source_bytes(name=name)
    return schema_module.parse_workflow_source_bytes(
        path,
        workflow_bytes=workflow_bytes,
        sidecar_bytes=sidecar,
        source=source,
        precedence=precedence,
    )


def test_current_v4_compilation_preserves_accepted_yaml_native_scalars(
    tmp_path,
) -> None:
    """Catch v4's internal bounds encoder rejecting values accepted by v1-v3."""
    from datetime import date
    import math

    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow

    path = tmp_path / "native-scalars.yaml"
    workflow_bytes = b"""\
name: native-scalars
description: Accepted YAML-native scalar compatibility
sandbox:
  expires: 2026-01-01
  ratio: .inf
  token: !!binary |
    AAEC
nodes:
  - id: run
    bash: "true"
    sandbox:
      expires: 2026-01-02
      ratio: .nan
      token: !!binary |
        AwQF
"""
    source = schema_module.parse_workflow_source_bytes(
        path,
        workflow_bytes=workflow_bytes,
        sidecar_bytes=b"language_compatibility: archon-2026-07\n",
        source="explicit",
        precedence=0,
    )

    compilation = compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
    )

    assert compilation.package.language.normalizer_version == 4
    root_sandbox = compilation.package.definition.options["sandbox"]
    assert root_sandbox["expires"] == date(2026, 1, 1)
    assert root_sandbox["ratio"] == math.inf
    assert root_sandbox["token"] == b"\x00\x01\x02"
    node_sandbox = compilation.package.definition.nodes[0].options["sandbox"]
    assert node_sandbox["expires"] == date(2026, 1, 2)
    assert math.isnan(node_sandbox["ratio"])
    assert node_sandbox["token"] == b"\x03\x04\x05"
    assert len(compilation.composite_digest) == 64


def test_v4_native_scalar_tags_cannot_alias_literal_user_mappings(tmp_path) -> None:
    from datetime import date

    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
    from plugins.workflow.dependency_manifest import _logical_graph_value

    assert _logical_graph_value(date(2026, 1, 1)) != _logical_graph_value({
        "$hermes_yaml_date": "2026-01-01",
    })

    sources = []
    for name, value in (
        ("native-date", "2026-01-01"),
        ("literal-tag", "{$hermes_yaml_date: '2026-01-01'}"),
    ):
        path = tmp_path / f"{name}.yaml"
        sources.append(
            schema_module.parse_workflow_source_bytes(
                path,
                workflow_bytes=(
                    f"name: {name}\n"
                    "description: Canonical tag separation\n"
                    f"sandbox:\n  marker: {value}\n"
                    "nodes:\n  - id: run\n    bash: 'true'\n"
                ).encode(),
                sidecar_bytes=b"language_compatibility: archon-2026-07\n",
                source="explicit",
                precedence=0,
            )
        )
    catalog = WorkflowCatalogSnapshot.capture(sources)
    native, literal = (
        compile_workflow(source, catalog, normalizer_version=4)
        for source in sources
    )

    assert (
        native.dependency_manifest.expanded_definition_digest
        != literal.dependency_manifest.expanded_definition_digest
    )
    assert native.composite_digest != literal.composite_digest


def test_v4_include_compilation_keeps_yaml_native_scalar_meaning(
    tmp_path,
) -> None:
    from datetime import date

    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow

    root_path = tmp_path / "root.yaml"
    child_path = tmp_path / "child.yaml"
    root = schema_module.parse_workflow_source_bytes(
        root_path,
        workflow_bytes=b"""\
name: native-root
description: Native values with an include
sandbox:
  expires: 2026-01-01
  token: !!binary |
    AAEC
nodes:
  - id: child
    include: native-child
""",
        sidecar_bytes=b"language_compatibility: archon-2026-07\n",
        source="explicit",
        precedence=0,
    )
    child = schema_module.parse_workflow_source_bytes(
        child_path,
        workflow_bytes=b"""\
name: native-child
description: Included child
nodes:
  - id: run
    bash: "true"
""",
        sidecar_bytes=None,
        source="explicit",
        precedence=0,
    )

    compilation = compile_workflow(
        root,
        WorkflowCatalogSnapshot.capture((root, child)),
        normalizer_version=4,
    )
    reparsed = schema_module.parse_workflow_source_bytes(
        tmp_path / "expanded.yaml",
        workflow_bytes=compilation.definition_bytes,
        sidecar_bytes=b"language_compatibility: archon-2026-07\n",
    )

    assert compilation.package.definition.options["sandbox"] == {
        "expires": date(2026, 1, 1),
        "token": b"\x00\x01\x02",
    }
    assert reparsed.options["sandbox"] == {
        "expires": date(2026, 1, 1),
        "token": b"\x00\x01\x02",
    }


def test_catalog_snapshot_selects_precedence_and_seals_content_signatures(
    tmp_path,
) -> None:
    """Catch catalog capture selecting by iteration order or mutable source state."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot

    profile = _parse_source(
        tmp_path / "profile" / "shared.yaml",
        name="shared",
        source="profile",
        precedence=2,
    )
    project = _parse_source(
        tmp_path / "project" / "shared.yaml",
        name="shared",
        source="project",
        precedence=1,
    )
    snapshot = WorkflowCatalogSnapshot.capture((profile, project))

    assert snapshot.select("shared") is project
    assert snapshot.select("shared", catalog_source="project") is project
    with pytest.raises(KeyError):
        snapshot.select("shared", catalog_source="profile")
    assert len(snapshot.signatures["shared"]) == 64
    with pytest.raises(TypeError):
        snapshot.selected["other"] = profile
    with pytest.raises(TypeError):
        snapshot.signatures["shared"] = "0" * 64


def test_catalog_snapshot_records_same_precedence_ambiguity(tmp_path) -> None:
    """Catch duplicate names being silently selected by filesystem order."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot

    first = _parse_source(
        tmp_path / "one" / "duplicate.yaml",
        name="duplicate",
        source="project",
        precedence=1,
    )
    second = _parse_source(
        tmp_path / "two" / "duplicate.yaml",
        name="duplicate",
        source="project",
        precedence=1,
    )

    snapshot = WorkflowCatalogSnapshot.capture((second, first))

    assert snapshot.ambiguous_names == frozenset({"duplicate"})
    assert "duplicate" not in snapshot.selected
    with pytest.raises(KeyError):
        snapshot.select("duplicate")


def test_no_include_compilation_matches_legacy_loader_and_uses_only_root_policy(
    tmp_path,
) -> None:
    """Catch child declarations influencing root-profile compilation before includes."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow

    root_path = tmp_path / "root" / "report.yaml"
    root_bytes = _source_bytes(name="report")
    root_sidecar = b"language_compatibility: archon-2026-07\n"
    root = schema_module.parse_workflow_source_bytes(
        root_path,
        workflow_bytes=root_bytes,
        sidecar_bytes=root_sidecar,
        source="project",
        precedence=1,
    )
    child = _parse_source(
        tmp_path / "child" / "child.yaml",
        name="child",
        source="profile",
        precedence=2,
        sidecar=b"language_compatibility: hermes-legacy\n",
    )
    catalog = WorkflowCatalogSnapshot.capture((root, child))

    compiled = compile_workflow(root, catalog, normalizer_version=4)
    legacy = schema_module.load_workflow_snapshot(
        root_path,
        workflow_bytes=root_bytes,
        sidecar_bytes=root_sidecar,
        source="project",
        precedence=1,
        normalizer_version=4,
    )

    assert compiled.package == legacy
    assert compiled.package.language.normalizer_version == 4
    assert compiled.definition_bytes == root_bytes
    assert compiled.active_policy_bytes == root_sidecar


def test_compiled_cache_identity_includes_ordered_catalog_signatures(tmp_path) -> None:
    """Catch compiled roots being cached solely by unchanged root bytes."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow

    root = _parse_source(
        tmp_path / "root.yaml",
        name="root",
        source="project",
        precedence=1,
        sidecar=b"language_compatibility: archon-2026-07\n",
    )
    child = _parse_source(
        tmp_path / "child.yaml",
        name="child",
        source="profile",
        precedence=2,
    )
    first_catalog = WorkflowCatalogSnapshot.capture((root, child))
    first = compile_workflow(root, first_catalog, normalizer_version=4)
    repeated = compile_workflow(root, first_catalog, normalizer_version=4)
    changed_child = schema_module.parse_workflow_source_bytes(
        child.workflow_path,
        workflow_bytes=child.definition_bytes.replace(b"Child", b"Other"),
        sidecar_bytes=None,
        source=child.source,
        precedence=child.precedence,
    )
    changed_catalog = WorkflowCatalogSnapshot.capture((root, changed_child))
    changed = compile_workflow(root, changed_catalog, normalizer_version=4)

    assert repeated is first
    assert changed is not first
    assert changed.package == first.package


def test_compiled_cache_is_bounded_lru_and_has_an_explicit_clear_path(
    tmp_path, monkeypatch
) -> None:
    """Catch process-lifetime compilation retaining every admitted byte stream."""
    import plugins.workflow.compilation as compilation_module

    monkeypatch.setattr(compilation_module, "COMPILED_ROOT_CACHE_MAX_ENTRIES", 2)
    compilation_module.clear_compilation_cache()
    first_source = _parse_source(
        tmp_path / "first.yaml", name="first", source="project", precedence=1
    )
    second_source = _parse_source(
        tmp_path / "second.yaml", name="second", source="project", precedence=1
    )
    third_source = _parse_source(
        tmp_path / "third.yaml", name="third", source="project", precedence=1
    )
    first_catalog = compilation_module.WorkflowCatalogSnapshot.capture((first_source,))
    second_catalog = compilation_module.WorkflowCatalogSnapshot.capture(
        (second_source,)
    )
    third_catalog = compilation_module.WorkflowCatalogSnapshot.capture((third_source,))

    first = compilation_module.compile_workflow(first_source, first_catalog)
    second = compilation_module.compile_workflow(second_source, second_catalog)
    assert compilation_module.compile_workflow(first_source, first_catalog) is first
    compilation_module.compile_workflow(third_source, third_catalog)

    assert (
        compilation_module.compile_workflow(second_source, second_catalog) is not second
    )
    compilation_module.clear_compilation_cache()
    assert compilation_module.compile_workflow(first_source, first_catalog) is not first
    compilation_module.clear_compilation_cache()


def test_malformed_sidecar_precedes_definition_node_diagnostics(tmp_path) -> None:
    """Catch source parsing changing the legacy first-diagnostic contract."""
    path = tmp_path / "invalid.yaml"
    workflow_bytes = b"name: invalid\ndescription: Invalid\nnodes: []\n"
    sidecar_bytes = b"language_compatibility: [\n"
    path.write_bytes(workflow_bytes)
    path.with_name("invalid.hermes.yaml").write_bytes(sidecar_bytes)

    with pytest.raises(WorkflowValidationError) as disk_exc:
        schema_module.load_workflow(path)
    with pytest.raises(WorkflowValidationError) as snapshot_exc:
        schema_module.load_workflow_snapshot(
            path,
            workflow_bytes=workflow_bytes,
            sidecar_bytes=sidecar_bytes,
        )

    assert disk_exc.value.issues[0].code == "invalid_sidecar"
    assert snapshot_exc.value.issues[0].code == "invalid_sidecar"


def test_unsupported_sidecar_profile_precedes_definition_node_diagnostics(
    tmp_path,
) -> None:
    """Catch deferred language resolution changing the legacy first diagnostic."""
    path = tmp_path / "unsupported.yaml"
    workflow_bytes = b"name: unsupported\ndescription: Unsupported\nnodes: []\n"
    sidecar_bytes = b"language_compatibility: future-profile\n"
    path.write_bytes(workflow_bytes)
    path.with_name("unsupported.hermes.yaml").write_bytes(sidecar_bytes)

    with pytest.raises(WorkflowValidationError) as disk_exc:
        schema_module.load_workflow(path)
    with pytest.raises(WorkflowValidationError) as snapshot_exc:
        schema_module.load_workflow_snapshot(
            path,
            workflow_bytes=workflow_bytes,
            sidecar_bytes=sidecar_bytes,
        )

    assert disk_exc.value.issues[0].code == "workflow_language_profile_unsupported"
    assert snapshot_exc.value.issues[0].code == "workflow_language_profile_unsupported"


def test_public_loaders_parse_source_before_compiling(tmp_path, monkeypatch) -> None:
    """Catch compatibility loaders retaining a second parse/normalize implementation."""
    path = tmp_path / "legacy.yaml"
    path.write_bytes(_source_bytes(name="legacy"))
    calls = 0
    parse = schema_module.parse_workflow_source_bytes

    def counting_parse(*args, **kwargs):
        nonlocal calls
        calls += 1
        return parse(*args, **kwargs)

    monkeypatch.setattr(schema_module, "parse_workflow_source_bytes", counting_parse)

    schema_module.load_workflow(path)
    schema_module.load_workflow_snapshot(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=None,
    )

    assert calls == 2


def test_discovery_parse_cache_tracks_definition_and_sidecar_content_identity(
    tmp_path, workflow_writer, monkeypatch
) -> None:
    """Catch source caching by root path/stat metadata instead of both byte streams."""
    import plugins.workflow.discovery as discovery_module

    discovery_module.clear_discovery_cache()
    workdir = tmp_path / "repo"
    path = workflow_writer(
        workdir / ".hermes" / "workflows",
        name="cached-source",
        description="first-value",
    )
    sidecar = path.with_name(f"{path.stem}.hermes.yaml")
    sidecar.write_text("language_compatibility: archon-2026-07\n", encoding="utf-8")
    calls = 0
    parse = schema_module.parse_workflow_source_bytes

    def counting_parse(*args, **kwargs):
        nonlocal calls
        calls += 1
        return parse(*args, **kwargs)

    monkeypatch.setattr(
        discovery_module,
        "parse_workflow_source_bytes",
        counting_parse,
        raising=False,
    )

    first = discovery_module.discover_workflows(
        workdir, tmp_path / "profile", tmp_path / "home"
    )[0]
    repeated = discovery_module.discover_workflows(
        workdir, tmp_path / "profile", tmp_path / "home"
    )[0]
    path.write_bytes(path.read_bytes().replace(b"first-value", b"other-value"))
    changed_definition = discovery_module.discover_workflows(
        workdir, tmp_path / "profile", tmp_path / "home"
    )[0]
    sidecar.write_text("language_compatibility: hermes-legacy\n", encoding="utf-8")
    changed_sidecar = discovery_module.discover_workflows(
        workdir, tmp_path / "profile", tmp_path / "home"
    )[0]

    assert repeated is first
    assert changed_definition.definition.description == "other-value"
    assert changed_sidecar.language.effective_profile.value == "hermes-legacy"
    assert calls == 3
