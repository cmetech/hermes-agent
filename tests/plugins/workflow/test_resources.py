from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType

import pytest

from plugins.workflow.models import TerminalJournalReserve
from plugins.workflow import output_resolution
from plugins.workflow import scheduler as workflow_scheduler
from plugins.workflow.machine_contract import projection_was_truncated
from plugins.workflow.resources import ResourceResolver, VariableContext


def test_local_command_precedes_global_and_preserves_frontmatter(tmp_path: Path):
    local = tmp_path / "local"
    global_root = tmp_path / "global"
    (local / "commands").mkdir(parents=True)
    (global_root / "commands").mkdir(parents=True)
    (global_root / "commands" / "investigate.md").write_text("global")
    (local / "commands" / "investigate.md").write_text(
        "---\ndescription: Local investigation\nargument-hint: <topic>\n---\nlocal $ARGUMENTS\n"
    )

    command = ResourceResolver(local, global_root=global_root).command("investigate")

    assert command.body == "local $ARGUMENTS\n"
    assert command.description == "Local investigation"
    assert command.argument_hint == "<topic>"
    assert command.path == (local / "commands" / "investigate.md").resolve()


def test_resolved_node_output_is_an_immutable_complete_identity():
    resolved = output_resolution.ResolvedNodeOutput(
        canonical_bytes=b'{"items":[{"count":3}]}',
        value={"items": [{"count": 3}]},
        text='{"items":[{"count":3}]}',
        media_type="application/json",
        sha256="1" * 64,
        node_id="collect",
        attempt_id="attempt-winner",
        publication_id=None,
    )

    with pytest.raises((AttributeError, TypeError)):
        resolved.node_id = "other"
    with pytest.raises(TypeError):
        resolved.value["items"] = ()
    with pytest.raises(TypeError):
        resolved.value["items"][0]["count"] = 4


def test_archon_resolver_uses_canonical_candidate_identity_and_verified_bytes(
    tmp_path: Path,
):
    canonical = b'{"items":[{"count":3}],"ok":true}'
    output = tmp_path / "nodes" / "node" / "attempt" / "output.json"
    output.parent.mkdir(parents=True)
    output.write_bytes(canonical)
    digest = hashlib.sha256(canonical).hexdigest()
    candidate = output_resolution.PrimaryOutputCandidate(
        attempt_relative_path="nodes/node/attempt/output.json",
        media_type="application/json",
        size_bytes=len(canonical),
        sha256=digest,
        structured_value={"items": [{"count": 3}], "ok": True},
        schema_fingerprint="2" * 64,
        canonicalization_version=1,
        output_type=None,
    )
    descriptor = {
        "node_id": "collect",
        "attempt_id": "attempt-winner",
        "relative_path": candidate.attempt_relative_path,
        "media_type": candidate.media_type,
        "size_bytes": candidate.size_bytes,
        "sha256": candidate.sha256,
    }

    resolved = output_resolution.resolve_node_output(
        run_directory=tmp_path,
        node_id="collect",
        attempt_id="attempt-winner",
        descriptor=descriptor,
        candidate=candidate,
        publication_id=None,
    )

    assert resolved.canonical_bytes == canonical
    assert resolved.value["items"][0]["count"] == 3
    assert resolved.text == canonical.decode("utf-8")
    assert resolved.media_type == "application/json"
    assert resolved.sha256 == digest
    assert resolved.node_id == "collect"
    assert resolved.attempt_id == "attempt-winner"
    assert resolved.publication_id is None


def test_archon_resolver_reads_once_from_a_bounded_no_follow_descriptor(
    tmp_path: Path, monkeypatch
):
    canonical = b'{"ok":true}'
    output = tmp_path / "nodes" / "node" / "attempt" / "output.json"
    output.parent.mkdir(parents=True)
    output.write_bytes(canonical)
    held = output.with_name("held.json")
    outside = tmp_path / "outside.json"
    outside.write_bytes(b'{"no":true}')
    original_read = os.read
    read_sizes = []
    injected = False

    def swap_path_after_open(descriptor, size):
        nonlocal injected
        read_sizes.append(size)
        if not injected:
            output.rename(held)
            output.symlink_to(outside)
            injected = True
        return original_read(descriptor, size)

    monkeypatch.setattr(output_resolution.os, "read", swap_path_after_open)

    resolved = output_resolution.resolve_node_output(
        run_directory=tmp_path,
        node_id="collect",
        attempt_id="attempt-winner",
        descriptor={
            "node_id": "collect",
            "attempt_id": "attempt-winner",
            "relative_path": "nodes/node/attempt/output.json",
            "media_type": "application/json",
            "size_bytes": len(canonical),
            "sha256": hashlib.sha256(canonical).hexdigest(),
        },
    )

    assert injected
    assert output.is_symlink()
    assert read_sizes and max(read_sizes) <= len(canonical) + 1
    assert resolved.canonical_bytes == canonical
    assert resolved.value["ok"] is True


def test_archon_resolver_fails_closed_without_safe_descriptor_io(
    tmp_path: Path, monkeypatch
):
    canonical = b'{"ok":true}'
    (tmp_path / "output.json").write_bytes(canonical)
    monkeypatch.setattr(output_resolution, "_HAS_DESCRIPTOR_RELATIVE_IO", False)

    with pytest.raises(
        output_resolution.ArchonOutputIntegrityError,
        match="descriptor-relative.*reading is unavailable",
    ):
        output_resolution.resolve_node_output(
            run_directory=tmp_path,
            node_id="collect",
            attempt_id="attempt-winner",
            descriptor={
                "node_id": "collect",
                "attempt_id": "attempt-winner",
                "relative_path": "output.json",
                "media_type": "application/json",
                "size_bytes": len(canonical),
                "sha256": hashlib.sha256(canonical).hexdigest(),
            },
        )


def test_primary_output_candidate_identity_preserves_open_output_type_boundary():
    output_type = "MixedCase/分析/" + ("Ω" * (16_384 - len("MixedCase/分析/")))
    identity = {
        "attempt_relative_path": "nodes/work/attempt/output.json",
        "media_type": "application/json",
        "size_bytes": 11,
        "sha256": "1" * 64,
        "schema_fingerprint": "2" * 64,
        "canonicalization_version": 1,
        "output_type": output_type,
    }

    restored = output_resolution.primary_output_candidate_from_identity(identity)

    assert restored.output_type == output_type
    assert output_resolution.primary_output_candidate_identity(restored) == identity
    assert not projection_was_truncated(identity)


def test_primary_output_candidate_owns_compact_copies_of_nested_proxy_mappings():
    backing = dict.fromkeys(range(100_000))
    while backing:
        backing.popitem()
    assert sys.getsizeof(backing) > 1_000_000
    hostile_proxies = tuple(MappingProxyType(backing) for _ in range(128))

    candidate = output_resolution.PrimaryOutputCandidate(
        attempt_relative_path="output.json",
        media_type="application/json",
        size_bytes=2,
        sha256=hashlib.sha256(b"{}").hexdigest(),
        structured_value=hostile_proxies,
        schema_fingerprint=None,
        canonicalization_version=1,
        output_type=None,
    )

    assert candidate.structured_value is not hostile_proxies
    assert all(
        owned is not supplied
        for owned, supplied in zip(
            candidate.structured_value, hostile_proxies, strict=True
        )
    )
    backing["late-mutation"] = True
    assert all(dict(owned) == {} for owned in candidate.structured_value)
    owned_graph_size = (
        sys.getsizeof(candidate)
        + sys.getsizeof(candidate.structured_value)
        + sum(
            sys.getsizeof(owned) + sys.getsizeof(dict(owned))
            for owned in candidate.structured_value
        )
    )
    cache_weight = workflow_scheduler._primary_candidate_weight(
        ("run", "node", "attempt"), candidate
    )
    assert owned_graph_size <= cache_weight
    assert cache_weight < workflow_scheduler._OUTPUT_RESOLUTION_CACHE_MAX_BYTES


def test_primary_output_candidate_rejects_cyclic_structured_values_safely():
    cyclic = {}
    cyclic["self"] = (cyclic,)

    with pytest.raises(
        output_resolution.ArchonOutputIntegrityError,
        match="structured value is invalid",
    ):
        output_resolution.PrimaryOutputCandidate(
            attempt_relative_path="output.json",
            media_type="application/json",
            size_bytes=2,
            sha256=hashlib.sha256(b"{}").hexdigest(),
            structured_value=cyclic,
            schema_fingerprint=None,
            canonicalization_version=1,
            output_type=None,
        )


@pytest.mark.parametrize(
    "structured_value",
    (object(), {1: "value"}, {"value": {1, 2}}, float("inf")),
    ids=("object", "non-string-key", "set", "non-finite-number"),
)
def test_primary_output_candidate_rejects_non_json_structured_values(
    structured_value,
):
    with pytest.raises(
        output_resolution.ArchonOutputIntegrityError,
        match="structured value is invalid",
    ):
        output_resolution.PrimaryOutputCandidate(
            attempt_relative_path="output.json",
            media_type="application/json",
            size_bytes=2,
            sha256=hashlib.sha256(b"{}").hexdigest(),
            structured_value=structured_value,
            schema_fingerprint=None,
            canonicalization_version=1,
            output_type=None,
        )


@pytest.mark.parametrize(
    "relative_path",
    (
        "output.json\0",
        "./output.json",
        "nodes//output.json",
        "nodes/./output.json",
        "nodes/output.json/",
    ),
)
def test_primary_output_candidate_rejects_noncanonical_paths(relative_path):
    identity = {
        "attempt_relative_path": relative_path,
        "media_type": "application/json",
        "size_bytes": 2,
        "sha256": hashlib.sha256(b"{}").hexdigest(),
        "schema_fingerprint": None,
        "canonicalization_version": 1,
        "output_type": None,
    }

    with pytest.raises(
        output_resolution.ArchonOutputIntegrityError,
        match="candidate identity is invalid",
    ):
        output_resolution.primary_output_candidate_from_identity(identity)


@pytest.mark.parametrize(
    "output_type", (object(), 42, "", " \t ", "x" * 16_385)
)
def test_primary_output_candidate_identity_rejects_invalid_or_oversized_output_type(
    output_type,
):
    identity = {
        "attempt_relative_path": "nodes/work/attempt/output.json",
        "media_type": "application/json",
        "size_bytes": 11,
        "sha256": "1" * 64,
        "schema_fingerprint": "2" * 64,
        "canonicalization_version": 1,
        "output_type": output_type,
    }

    with pytest.raises(output_resolution.ArchonOutputIntegrityError):
        output_resolution.primary_output_candidate_from_identity(identity)


def test_archon_resolver_rejects_candidate_descriptor_digest_disagreement(
    tmp_path: Path,
):
    canonical = b'{"ok":true}'
    output = tmp_path / "output.json"
    output.write_bytes(canonical)
    candidate = output_resolution.PrimaryOutputCandidate(
        attempt_relative_path="output.json",
        media_type="application/json",
        size_bytes=len(canonical),
        sha256=hashlib.sha256(canonical).hexdigest(),
        structured_value={"ok": True},
        schema_fingerprint="2" * 64,
        canonicalization_version=1,
        output_type=None,
    )

    with pytest.raises(output_resolution.ArchonOutputIntegrityError):
        output_resolution.resolve_node_output(
            run_directory=tmp_path,
            node_id="collect",
            attempt_id="attempt-winner",
            descriptor={
                "node_id": "collect",
                "attempt_id": "attempt-winner",
                "relative_path": "output.json",
                "media_type": "application/json",
                "size_bytes": len(canonical),
                "sha256": "0" * 64,
            },
            candidate=candidate,
            publication_id=None,
        )


def test_archon_text_output_keeps_phase2_json_field_compatibility(tmp_path: Path):
    data = b'{"summary":{"count":3}}'
    output = tmp_path / "stdout.txt"
    output.write_bytes(data)
    resolved = output_resolution.resolve_node_output(
        run_directory=tmp_path,
        node_id="collect",
        attempt_id="attempt-winner",
        descriptor={
            "node_id": "collect",
            "attempt_id": "attempt-winner",
            "relative_path": "stdout.txt",
            "media_type": "text/plain",
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
    )

    variables = VariableContext(node_outputs={"collect": resolved})

    assert variables.render_prompt("$collect.output") == data.decode("utf-8")
    assert variables.render_prompt("$collect.output.summary.count") == "3"


@pytest.mark.parametrize("mutation", ["delete", "rename", "replace"])
def test_command_uses_authenticated_bytes_without_reopening_source(
    tmp_path: Path, mutation: str
):
    root = tmp_path / "run"
    command = root / "commands" / "investigate.md"
    command.parent.mkdir(parents=True)
    authenticated = b"authenticated instructions\n"
    command.write_bytes(authenticated)
    resolver = ResourceResolver(
        root,
        sealed_paths={"commands/investigate.md"},
        sealed_bytes={"commands/investigate.md": authenticated},
    )
    if mutation == "delete":
        command.unlink()
    elif mutation == "rename":
        command.rename(command.with_suffix(".gone"))
    else:
        command.write_text("forged instructions\n", encoding="utf-8")

    resolved = resolver.command("investigate")

    assert resolved.body == "authenticated instructions\n"


@pytest.mark.parametrize(
    "name", ("../secret", "nested/secret", "/tmp/secret", "~/.secret")
)
def test_command_name_cannot_traverse(tmp_path: Path, name: str):
    with pytest.raises(ValueError, match="contained command name"):
        ResourceResolver(tmp_path).command(name)


def test_prompt_substitution_is_single_pass_and_supports_json_dot_references(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("SECRET_TOKEN", "must-not-expand")
    variables = VariableContext(
        arguments="alpha beta",
        user_message="user $ARGUMENTS",
        artifacts_dir=tmp_path / "artifacts",
        workflow_id="wf-1",
        base_branch="base",
        docs_dir=tmp_path / "docs",
        context="shared context",
        loop_user_input="continue",
        rejection_reason="needs evidence",
        node_outputs={"collect": json.dumps({"summary": {"count": 3}, "value": "ok"})},
    )
    template = (
        "$ARGUMENTS|$USER_MESSAGE|$1|$2|$ARTIFACTS_DIR|$WORKFLOW_ID|"
        "$BASE_BRANCH|$DOCS_DIR|$CONTEXT|$LOOP_USER_INPUT|$REJECTION_REASON|"
        "$collect.output|$collect.output.summary.count|$SECRET_TOKEN"
    )

    rendered = variables.render_prompt(template)

    assert rendered.split("|")[:11] == [
        "alpha beta",
        "user $ARGUMENTS",
        "alpha",
        "beta",
        str(tmp_path / "artifacts"),
        "wf-1",
        "base",
        str(tmp_path / "docs"),
        "shared context",
        "continue",
        "needs evidence",
    ]
    assert '"count": 3' in rendered
    assert "|3|$SECRET_TOKEN" in rendered
    assert "must-not-expand" not in rendered


def test_prompt_and_bash_render_from_one_resolved_value_without_reparsing(
    tmp_path: Path,
):
    canonical = b'{"items":[{"count":3}],"ok":true}'
    resolved = output_resolution.ResolvedNodeOutput(
        canonical_bytes=canonical,
        value={"items": [{"count": 3}], "ok": True},
        text=canonical.decode("utf-8"),
        media_type="application/json",
        sha256=hashlib.sha256(canonical).hexdigest(),
        node_id="collect",
        attempt_id="attempt-winner",
        publication_id="publication-1",
    )
    variables = VariableContext(node_outputs={"collect": resolved})

    prompt = variables.render_prompt(
        "$collect.output|$collect.output.items.0|$collect.output.items|"
        "$collect.output.items.0.count|$collect.output.ok"
    )
    bash = variables.render_bash(
        "printf '%s|%s|%s|%s|%s' $collect.output "
        "$collect.output.items.0 $collect.output.items "
        "$collect.output.items.0.count $collect.output.ok",
        spill_directory=tmp_path / "spill",
    )
    completed = subprocess.run(
        ["/bin/sh", "-c", bash],
        check=True,
        capture_output=True,
        text=True,
    )

    expected = f'{resolved.text}|{{"count":3}}|[{{"count":3}}]|3|true'
    assert prompt == expected
    assert completed.stdout == expected
    assert variables.node_outputs["collect"] is resolved


def test_bash_substitution_quotes_values_and_spills_large_values(tmp_path: Path):
    spill = tmp_path / "spill"
    variables = VariableContext(
        arguments="$(touch injected) 'quoted'",
        workflow_id="wf",
        node_outputs={"collect": "x" * 100},
    )

    rendered = variables.render_bash(
        "printf '%s' $ARGUMENTS; cat $collect.output",
        spill_directory=spill,
        max_inline_chars=64,
    )

    spilled = tuple(spill.iterdir())
    assert len(spilled) == 1
    assert spilled[0].read_text() == "x" * 100
    assert str(spilled[0]) in rendered
    subprocess.run(["/bin/sh", "-c", rendered], cwd=tmp_path, check=True)
    assert not (tmp_path / "injected").exists()


def test_bash_substitution_preserves_values_inside_existing_shell_quotes(
    tmp_path: Path,
):
    variables = VariableContext(arguments='a "double" $HOME `date` \\ tail')

    rendered = variables.render_bash(
        "printf '%s' \"$ARGUMENTS\"",
        spill_directory=tmp_path / "spill",
    )
    completed = subprocess.run(
        ["/bin/sh", "-c", rendered],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout == variables.arguments


def test_terminal_journal_reserve_bounds_projection_growth_and_terminal_frames():
    reserve = TerminalJournalReserve.for_projection(4_096)

    assert reserve.projection_limit_bytes >= 4_096
    assert reserve.terminal_reserve_bytes >= 3 * reserve.projection_limit_bytes
    assert reserve.contains_projection(4_096)
    assert not reserve.contains_projection(reserve.projection_limit_bytes + 1)

    with pytest.raises(ValueError, match="projection_bytes"):
        TerminalJournalReserve.for_projection(0)
