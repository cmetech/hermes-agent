from __future__ import annotations

import os

import pytest

from plugins.workflow.bash_rendering import (
    BashRenderingError,
    classify_bash_reference_spans,
)
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.executors.bash import BashExecutor
from plugins.workflow.models import WorkflowLanguageProfile, WorkflowNode, freeze_value
from plugins.workflow.resources import VariableContext, substitution_renderer
from plugins.workflow.schema import WorkflowValidationError, load_workflow


def _archon_bash_package(workflow_writer, root, command: str):
    workflow = workflow_writer(
        root,
        name="bash-lexer-security",
        nodes=[
            {"id": "producer", "prompt": "produce"},
            {
                "id": "consumer",
                "bash": command,
                "depends_on": ["producer"],
            },
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    return load_workflow(workflow)


def _execute_scalar(tmp_path, *, command: str, value: str, spawn_intent=None):
    node = WorkflowNode(
        id="shell",
        node_type="bash",
        value=command,
        depends_on=(),
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )
    renderer = substitution_renderer(
        VariableContext(user_message=value, normalizer_version=3),
        direct_dependencies=(),
    )
    result = BashExecutor().execute(
        NodeExecutionContext(
            run_id="bash-lexer-security",
            run_directory=tmp_path,
            node=node,
            attempt_id="attempt-1",
            variable_context=renderer,
            language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=3,
            spawn_intent=spawn_intent,
            max_output_bytes=600_000,
        )
    )
    stdout = tmp_path / "nodes" / "shell" / "attempt-1" / "stdout.txt"
    return result, stdout.read_bytes() if stdout.exists() else None


@pytest.mark.parametrize(
    "command",
    (
        "printf '%s' \"$(case x in x) printf '%s' $producer.output;; esac)\"",
        "printf '%s' \"$[$producer.output]\"",
        "printf '%s' $'$producer.output'",
        ("printf '%s' \"$(case x in x) cat <<EOF\n$producer.output\nEOF\n;; esac)\""),
        "printf '%s' \"$(time case x in x) printf $producer.output;; esac)\"",
        (
            "printf '%s' \"$(if :; then case x in x) "
            'printf $producer.output;; esac; fi)"'
        ),
        (
            "printf '%s' \"$(if case x in x) printf $producer.output;; "
            'esac; then :; fi)"'
        ),
        ("printf '%s' \"$(f() { case x in x) printf $producer.output;; esac; }; f)\""),
        (
            "printf '%s' \"$(function f { case x in x) "
            'printf $producer.output;; esac; }; f)"'
        ),
        (
            "printf '%s' \"$(function f-f { case x in x) "
            'printf $producer.output;; esac; }; f-f)"'
        ),
        "printf '%s' \"$([[ x =~ [)] ]] || printf $producer.output)\"",
        "printf '%s' \"$(coproc case x in x) printf $producer.output;; esac)\"",
        (
            "printf '%s' \"$(coproc worker case x in x) "
            'printf $producer.output;; esac)"'
        ),
    ),
    ids=(
        "nested-case",
        "legacy-arithmetic",
        "ansi-c",
        "nested-case-heredoc",
        "time-case",
        "if-case",
        "case-as-if-condition",
        "function-body-case",
        "alternate-function-case",
        "alternate-function-hyphen-case",
        "conditional-regex-paren",
        "coprocess-case",
        "named-coprocess-case",
    ),
)
def test_v3_admission_rejects_dialect_contexts_that_are_not_simple_tokens(
    tmp_path,
    workflow_writer,
    command,
) -> None:
    with pytest.raises(WorkflowValidationError) as exc:
        _archon_bash_package(workflow_writer, tmp_path, command)

    assert [issue.code for issue in exc.value.issues] == [
        "bash_reference_context_unsupported"
    ]
    assert exc.value.issues[0].path == "nodes[1].bash"


@pytest.mark.skipif(os.name == "nt", reason="real POSIX shell contract")
@pytest.mark.parametrize(
    "command",
    (
        "printf '%s' \"$(case x in x) printf '%s' $USER_MESSAGE;; esac)\"",
        "printf '%s' \"$[$USER_MESSAGE]\"",
        "printf '%s' $'$USER_MESSAGE'",
        ("printf '%s' \"$(f() { case x in x) printf $USER_MESSAGE;; esac; }; f)\""),
        (
            "printf '%s' \"$(function f { case x in x) "
            'printf $USER_MESSAGE;; esac; }; f)"'
        ),
        (
            "printf '%s' \"$(function f-f { case x in x) "
            'printf $USER_MESSAGE;; esac; }; f-f)"'
        ),
        "printf '%s' \"$([[ x =~ [)] ]] || printf $USER_MESSAGE)\"",
        "printf '%s' \"$(coproc case x in x) printf $USER_MESSAGE;; esac)\"",
        ("printf '%s' \"$(coproc worker case x in x) printf $USER_MESSAGE;; esac)\""),
    ),
    ids=(
        "nested-case",
        "legacy-arithmetic",
        "ansi-c",
        "function-body-case",
        "alternate-function-case",
        "alternate-function-hyphen-case",
        "conditional-regex-paren",
        "coprocess-case",
        "named-coprocess-case",
    ),
)
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_executor_rejects_dialect_contexts_before_launch_for_all_value_sizes(
    tmp_path,
    command,
    size,
) -> None:
    marker = tmp_path / "injected"
    payload = f"space * ; touch {marker}; " + "z" * size + "x\n\n"
    launched: list[str] = []

    result, output = _execute_scalar(
        tmp_path,
        command=command,
        value=payload,
        spawn_intent=lambda nonce: launched.append(nonce) or True,
    )

    assert result.status == "failed"
    assert result.error_code == "bash_reference_context_unsupported"
    assert launched == []
    assert output is None
    assert not marker.exists()


@pytest.mark.skipif(os.name == "nt", reason="real POSIX shell contract")
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_executor_does_not_substitute_the_second_dollar_of_pid_expansion(
    tmp_path,
    size,
) -> None:
    marker = tmp_path / "pid-expansion-injected"
    payload = f"(touch {marker})" + "x" * size
    launched: list[str] = []

    result, output = _execute_scalar(
        tmp_path,
        command="printf '%s' \"$$USER_MESSAGE\"",
        value=payload,
        spawn_intent=lambda nonce: launched.append(nonce) or True,
    )

    assert result.status == "succeeded"
    assert output is not None
    assert output.endswith(b"USER_MESSAGE")
    assert output[: -len(b"USER_MESSAGE")].isascii()
    assert output[: -len(b"USER_MESSAGE")].isdigit()
    assert payload.encode("utf-8") not in output
    assert len(launched) == 1
    assert not marker.exists()


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
@pytest.mark.parametrize(
    "command",
    (
        "case x in x) printf '%s' $USER_MESSAGE;; esac",
        "case x in x) printf '%s' \"$USER_MESSAGE\";; esac",
        "case x in x) printf '%s' '$USER_MESSAGE';; esac",
    ),
    ids=("unquoted", "double-quoted", "single-quoted"),
)
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_real_shell_keeps_exact_data_in_top_level_case_actions(
    tmp_path,
    command,
    size,
) -> None:
    value = "space * 'single' \"double\" $dollar `tick` ; " + "z" * size + "x\n\n"

    result, output = _execute_scalar(tmp_path, command=command, value=value)

    assert result.status == "succeeded"
    assert output == value.encode("utf-8")


@pytest.mark.parametrize(
    "template",
    (
        ": \"$(case x in x) printf '%s' case;; esac)\"; printf $USER_MESSAGE",
        ': "$(case x in x) :; esac)"; printf $USER_MESSAGE',
        ': "$(case x in x) :;& y) :;; esac)"; printf $USER_MESSAGE',
        ': "$[array[0]]"; printf $USER_MESSAGE',
        ": $'literal'; printf $USER_MESSAGE",
        ': "$(cat <<EOF\nliteral\nEOF\n)"; printf $USER_MESSAGE',
        ("cat <<OUT \"$(printf '%s\\n' inner\n)\"\nliteral\nOUT\nprintf $USER_MESSAGE"),
        ': "$([[ a =~ [[:alpha:]] ]] || :)"; printf $USER_MESSAGE',
        ": \"$([[ x == ']]' ]] || :)\"; printf $USER_MESSAGE",
        ': "$([[ $(printf x) == x ]] || :)"; printf $USER_MESSAGE',
    ),
    ids=(
        "case-action-argument",
        "case-final-body",
        "case-fallthrough",
        "legacy-arithmetic-restores-state",
        "ansi-c-restores-state",
        "nested-heredoc-restores-state",
        "outer-heredoc-scope",
        "conditional-posix-class-restores-state",
        "conditional-quoted-close-restores-state",
        "conditional-nested-command-restores-state",
    ),
)
def test_v3_lexer_restores_state_before_a_later_top_level_reference(
    template,
) -> None:
    start = template.index("$USER_MESSAGE")

    assert classify_bash_reference_spans(
        template,
        ((start, start + len("$USER_MESSAGE")),),
    ) == ((start, start + len("$USER_MESSAGE"), None),)


def test_v3_lexer_does_not_treat_ansi_c_punctuation_inside_double_quotes_as_quote() -> (
    None
):
    template = "printf '%s' \"$'$USER_MESSAGE'\""
    start = template.index("$USER_MESSAGE")

    assert classify_bash_reference_spans(
        template,
        ((start, start + len("$USER_MESSAGE")),),
    ) == ((start, start + len("$USER_MESSAGE"), '"'),)


def test_v3_lexer_preserves_non_special_backslashes_in_quoted_heredoc_delimiter() -> (
    None
):
    template = 'cat <<"E\\Q"\nEQ\n$USER_MESSAGE\nE\\Q\n'
    start = template.index("$USER_MESSAGE")

    with pytest.raises(BashRenderingError) as exc:
        classify_bash_reference_spans(
            template,
            ((start, start + len("$USER_MESSAGE")),),
        )

    assert exc.value.code == "bash_reference_context_unsupported"
