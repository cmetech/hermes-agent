from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from plugins.workflow.models import TerminalJournalReserve
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
