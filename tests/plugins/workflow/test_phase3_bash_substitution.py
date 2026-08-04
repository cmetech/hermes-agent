from __future__ import annotations

import hashlib
import json
import os
import stat
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import plugins.workflow.bash_rendering as bash_rendering
import plugins.workflow.language_schema as language_schema
import tools.managed_process as managed_process_module
from plugins.workflow.bash_rendering import (
    BashRenderingError,
    render_v3_bash,
)
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.executors.bash import BashExecutor
from plugins.workflow.models import WorkflowLanguageProfile, WorkflowNode, freeze_value
from plugins.workflow.output_resolution import ResolvedOutputReference
from plugins.workflow.resources import VariableContext, substitution_renderer
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import WorkflowValidationError, load_workflow
from tools.managed_process import ManagedProcessTree


_SHELL_CONTEXTS = {
    "unquoted": "printf '%s' $USER_MESSAGE",
    "double-quoted": "printf '%s' \"$USER_MESSAGE\"",
    "single-quoted": "printf '%s' '$USER_MESSAGE'",
}


def test_bash_runtime_bounds_are_reexported_from_the_language_contract() -> None:
    assert (
        bash_rendering.BASH_INLINE_MAX_BYTES
        == language_schema.BASH_INLINE_MAX_BYTES
        == 32_768
    )
    assert (
        bash_rendering.BASH_SPILL_MAX_VALUE_BYTES
        == language_schema.BASH_SPILL_MAX_VALUE_BYTES
        == 500_000
    )
    assert (
        bash_rendering.BASH_SPILL_MAX_FILES
        == language_schema.BASH_SPILL_MAX_FILES
        == 64
    )
    assert (
        bash_rendering.BASH_SPILL_MAX_TOTAL_BYTES
        == language_schema.BASH_SPILL_MAX_TOTAL_BYTES
        == 2_000_000
    )

_JOINED_UNSUPPORTED_CONTEXTS = (
    ("(\\\n( {reference} ))", "bare-arithmetic"),
    ("printf '%s' \"$\\\n(printf '%s' {reference})\"", "command-substitution"),
    ("printf '%s' \"$\\\n((1 + {reference}))\"", "arithmetic-expansion"),
    ("printf '%s' \"$\\\n{{OTHER:-{reference}}}\"", "parameter-expansion"),
    ("printf '%s' \"$\\\n[{reference}]\"", "legacy-arithmetic"),
    ("[\\\n[ -n {reference} ]]", "conditional-command"),
    ("cat <\\\n<EOF\n{reference}\nEOF\n", "heredoc"),
    (
        "printf ignored " + "\\\\" + "\n(( {reference} ))",
        "escaped-backslash-line-break",
    ),
)

_ARRAY_SUBSCRIPT_CONTEXTS = (
    ("items[{reference}]=9", "indexed-assignment"),
    ("items[{reference}]+=9", "indexed-augmented-assignment"),
    ("items=([{reference}]=9)", "compound-assignment"),
    ("items+=([{reference}]=9)", "compound-append-assignment"),
)

_PREFIXED_ARRAY_SUBSCRIPT_CONTEXTS = (
    ("> /dev/null items[{reference}]=9", "leading-redirection"),
    ("function f {{ items[{reference}]=9; }}; f", "function-keyword"),
    ("coproc {{ items[{reference}]=9; }}; wait", "direct-coproc"),
    ("coproc worker {{ items[{reference}]=9; }}; wait", "named-coproc"),
    ("command declare -a items[{reference}]=9", "command-declare"),
    ("command -p declare -a items[{reference}]=9", "command-p-declare"),
    ("command -- declare -a items[{reference}]=9", "command-dashdash-declare"),
    ("builtin declare -a items[{reference}]=9", "builtin-declare"),
    ("builtin -- declare -a items[{reference}]=9", "builtin-dashdash-declare"),
    ('declare -a "items[{reference}]=9"', "quoted-declare"),
    ('typeset -a "items[{reference}]=9"', "quoted-typeset"),
    (
        'function f {{ local -a "items[{reference}]=9"; }}; f',
        "quoted-local",
    ),
    ('readonly -a "items[{reference}]=9"', "quoted-readonly"),
    ('export "items[{reference}]=9"', "quoted-export"),
)

_FD_HEREDOC_ARRAY_SUBSCRIPT_CONTEXTS = tuple(
    (
        f"{descriptor}{operator}EOF items[{{reference}}]=9\n{tab}body\n{tab}EOF\n",
        f"fd-{descriptor}-{context}",
    )
    for descriptor in range(10)
    for operator, tab, context in (("<<", "", "heredoc"), ("<<-", "\t", "strip-tabs"))
) + (
    (
        ">/dev/null 0<<'ONE' 3>/dev/null 4<<-TWO "
        "items[{reference}]=9\none\nONE\n\ttwo\n\tTWO\n",
        "multiple-leading-redirections-and-heredocs",
    ),
)

_QUOTE_REMOVED_ARRAY_SUBSCRIPT_CONTEXTS = (
    ('"command" declare -a "items[{reference}]"=9', "double-wrapper"),
    ("'command' declare -a \"items[{reference}]\"=9", "single-wrapper"),
    ('comm\\and declare -a "items[{reference}]"=9', "escaped-wrapper"),
    ('c"om"mand -- d"ecl"are -a "items[{reference}]"=9', "concatenated-wrapper"),
    ('command "-p" "declare" -a "items[{reference}]"=9', "quoted-command-p-option"),
    ('command "--" "declare" -a "items[{reference}]"=9', "quoted-command-option"),
    ('builtin "--" "declare" -a "items[{reference}]"=9', "quoted-builtin-option"),
    ('"declare" -a "items[{reference}]"=9', "double-declare"),
    ("'typeset' -a \"items[{reference}]\"=9", "single-typeset"),
    ('l"oc"al -a "items[{reference}]"=9', "concatenated-local"),
    ('read\\only -a "items[{reference}]"=9', "escaped-readonly"),
    ('ex"port" "items[{reference}]"=9', "concatenated-export"),
    ('declare -a i"tems["{reference}"]"=9', "concatenated-assignment-word"),
)

_JOINED_HEREDOC_CONTEXTS = (
    (
        "cat <\\\n<-\\\n'EOF' >/dev/null\n\tline\\\n\tEOF\nprintf '%s' \"{reference}\"",
        "between-less-and-after-dash",
    ),
    (
        "cat <<\\\n-'EOF' >/dev/null\n\tline\\\n\tEOF\nprintf '%s' \"{reference}\"",
        "between-operator-and-dash",
    ),
    (
        "cat <<-\\\n'EOF' >/dev/null\n\tline\\\n\tEOF\nprintf '%s' \"{reference}\"",
        "after-dash",
    ),
    (
        "cat <<-EO\\\nF >/dev/null\n\tbody\n\tEOF\nprintf '%s' \"{reference}\"",
        "split-unquoted-delimiter",
    ),
    (
        ": 3<\\\n<-\\\n'ONE' 4<<-\\\n\"TWO\"\n"
        "\tone\\\n\tONE\n\ttwo\\\n\tTWO\n"
        "printf '%s' \"{reference}\"",
        "multiple-queued-strip-tabs",
    ),
)

_COMMENT_FOLLOWED_HEREDOC_DELIMITERS = (
    ("EOF", "unquoted"),
    ("'EOF'", "single-quoted"),
    ('"EOF"', "double-quoted"),
    ("\\EOF", "backslash-quoted"),
)

_FALSE_HEREDOC_TOKEN_CONTEXTS = (
    ('printf ignored "<<\\\n-EOF"; printf \'%s\' "$USER_MESSAGE"', "quoted"),
    ("(( shifted = 1 << 2 )); printf '%s' \"$USER_MESSAGE\"", "arithmetic"),
    ('[[ "<<-EOF" == "<<-EOF" ]]; printf \'%s\' "$USER_MESSAGE"', "conditional"),
    ("# false <<-EOF \\\nprintf '%s' \"$USER_MESSAGE\"", "comment"),
)

_PROCESS_SUBSTITUTION_CONTEXTS = (
    ("cat <(printf '%s' {reference})", "input"),
    ("cat >(printf '%s' {reference})", "output"),
    ("cat <(printf '%s' \"{reference}\")", "quoted-command-word"),
    ("cat <( ( printf '%s' {reference} ) )", "nested-parentheses"),
    ("cat <(cat >(printf '%s' {reference}))", "nested-process-substitution"),
    (
        "printf '%s' \"$(cat <(printf '%s' {reference}))\"",
        "outer-command-substitution",
    ),
)

_UNSAFE_WORD_EXPANSION_CONTEXTS = (
    ("printf '%s' @({reference}|x)", "extglob-at"),
    ("printf '%s' !({reference})", "extglob-not"),
    ("printf '%s' +({reference})", "extglob-plus"),
    ("printf '%s' ?({reference})", "extglob-question"),
    ("printf '%s' *({reference})", "extglob-star"),
    ("printf '%s' pre{{{reference},x}}post", "brace-first"),
    ("printf '%s' pre{{x,{reference}}}post", "brace-second"),
)

_HERE_STRING_CONTEXTS = (
    ("read value <<<{reference}; printf '%s' \"$value\"", "joined"),
    ("read value <<< {reference}; printf '%s' \"$value\"", "spaced"),
    ("read value <\\\n<<{reference}; printf '%s' \"$value\"", "join-first"),
    ("read value <<\\\n<{reference}; printf '%s' \"$value\"", "join-last"),
    (
        "read value <\\\n<\\\n<{reference}; printf '%s' \"$value\"",
        "join-both",
    ),
    ("read value <<<\\\n{reference}; printf '%s' \"$value\"", "join-operand"),
)

_PHASE_REENTRY_SAFE_CONTEXTS = (
    (
        "(( shifted = 1 << 2 )); cat <<'EOF' >/dev/null\n"
        "line\\\nEOF\nprintf '%s' \"{reference}\"",
        "arithmetic-shift-before-quoted-heredoc",
    ),
    (
        "[[ $((1 << 2)) -eq 4 ]]; cat <<'EOF' >/dev/null\n"
        "line\\\nEOF\nprintf '%s' \"{reference}\"",
        "conditional-arithmetic-before-quoted-heredoc",
    ),
    (
        "printf '%s' \"$(cat <<'EOF' >/dev/null\n"
        'line\\\nEOF\nprintf nested)"; printf \'%s\' "{reference}"',
        "quoted-heredoc-in-outer-double-quoted-command-substitution",
    ),
    (
        "printf '%s' \"`cat <<'EOF' >/dev/null\n"
        'line\\\nEOF\nprintf nested`"; printf \'%s\' "{reference}"',
        "quoted-heredoc-in-legacy-command-substitution",
    ),
)


_AUTHORED_END_FAIL_OPEN_TEMPLATES = (
    (
        "function",
        "printf '%s' \"$({token} f { case x in x) printf {reference};; esac; }; f)\"",
        "function",
    ),
    (
        "coproc",
        "printf '%s' \"$({token} { case x in x) printf {reference};; esac; }; wait)\"",
        "direct-coproc",
    ),
    (
        "coproc",
        "printf '%s' \"$({token} worker { case x in x) printf {reference};; esac; }; wait)\"",
        "named-coproc",
    ),
    (
        "then",
        "printf '%s' \"$(if true; {token} case x in x) printf {reference};; esac; fi)\"",
        "then-case",
    ),
)


def _continued_authored_end_fail_open_contexts():
    for word, template, context in _AUTHORED_END_FAIL_OPEN_TEMPLATES:
        for split in range(1, len(word)):
            authored = f"{word[:split]}\\\n{word[split:]}"
            yield pytest.param(
                template.replace("{token}", authored),
                id=f"{context}-split-{split}",
            )
        yield pytest.param(
            template.replace("{token}", f"{word}\\\n"),
            id=f"{context}-after",
        )


_AUTHORED_END_FAIL_OPEN_CONTEXTS = tuple(_continued_authored_end_fail_open_contexts())

_PRIOR_CONTINUATION_COMMENT_HEREDOC_CONTEXTS = tuple(
    (
        f": \\\n# comment \\\n<<{delimiter} >/dev/null\n{{reference}}\nEOF\n",
        delimiter_kind,
    )
    for delimiter, delimiter_kind in _COMMENT_FOLLOWED_HEREDOC_DELIMITERS
)

_ESCAPED_ARRAY_SUBSCRIPT_CONTEXTS = (
    ("items[\\{reference}]=9", "escaped-direct-subscript"),
    ("items=([\\{reference}]=9)", "escaped-compound-subscript"),
    ('"declare" -a "items[\\{reference}]"=9', "escaped-quoted-declare"),
    ('c"om"mand declare -a "items[\\{reference}]"=9', "escaped-concat-wrapper"),
)

_QUOTED_HEREDOC_DELIMITERS = (
    ("'EOF'", "single-quoted"),
    ('"EOF"', "double-quoted"),
    ("\\EOF", "backslash-quoted"),
)


def _run_v3_bash(
    tmp_path,
    *,
    command: str,
    value: str,
    spawn_intent=None,
    profile=WorkflowLanguageProfile.ARCHON_2026_07,
):
    node = WorkflowNode(
        id="shell",
        node_type="bash",
        value=command,
        depends_on=(),
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )
    variables = VariableContext(
        user_message=value,
        normalizer_version=3,
    )
    renderer = substitution_renderer(variables, direct_dependencies=())

    result = BashExecutor().execute(
        NodeExecutionContext(
            run_id="run-1",
            run_directory=tmp_path,
            node=node,
            attempt_id="attempt-1",
            max_output_bytes=600_000,
            variable_context=renderer,
            language_profile=profile,
            normalizer_version=3,
            spawn_intent=spawn_intent,
        )
    )
    output_path = tmp_path / "nodes" / "shell" / "attempt-1" / "stdout.txt"
    output = output_path.read_bytes() if output_path.exists() else None
    return result, output


def _archon_bash_package(workflow_writer, root, command: str, *, depends_on=()):
    workflow = workflow_writer(
        root,
        name="bash-admission-context",
        nodes=[
            {"id": "producer", "prompt": "produce"},
            {
                "id": "consumer",
                "bash": command,
                "depends_on": list(depends_on),
            },
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    return load_workflow(workflow)


def _scheduler_preflight_bash_template(
    tmp_path,
    monkeypatch,
    command: str,
    *,
    depends_on=(),
):
    store = MagicMock()
    store.run_directory.return_value = tmp_path
    scheduler = object.__new__(RunScheduler)
    scheduler.store = store
    node = WorkflowNode(
        id="shell",
        node_type="bash",
        value=command,
        depends_on=depends_on,
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )
    package = SimpleNamespace(
        language=SimpleNamespace(
            effective_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=3,
        )
    )
    monkeypatch.setattr(
        scheduler,
        "_revalidate_retained_output_resolution",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        scheduler,
        "_strict_reference_templates",
        lambda *_args, **_kwargs: (command,),
    )
    return scheduler._preflight_strict_node_references(
        "run-1",
        node,
        package,
        {"nodes": {"shell": {"state": "ready"}}},
        sealed_resource_paths=frozenset(),
        sealed_resource_bytes={},
    )


def _execute_rejected_bash_context(
    tmp_path,
    *,
    command: str,
    reference: str,
    size: int,
) -> None:
    value = "7" * size
    output_reference = reference == "$producer.output"
    dependencies = ("producer",) if output_reference else ()
    resolver = MagicMock(
        side_effect=AssertionError("unsupported output reference was resolved")
    )
    renderer = substitution_renderer(
        VariableContext(user_message=value, normalizer_version=3),
        direct_dependencies=dependencies,
        output_resolver=resolver if output_reference else None,
    )
    launched: list[str] = []
    node = WorkflowNode(
        id="shell",
        node_type="bash",
        value=command,
        depends_on=dependencies,
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )

    result = BashExecutor().execute(
        NodeExecutionContext(
            run_id="unsupported-context",
            run_directory=tmp_path,
            node=node,
            attempt_id="attempt-1",
            variable_context=renderer,
            language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=3,
            spawn_intent=lambda nonce: launched.append(nonce) or True,
        )
    )

    assert result.status == "failed"
    assert result.error_code == "bash_reference_context_unsupported"
    assert launched == []
    attempt = tmp_path / "nodes" / "shell" / "attempt-1"
    assert not (attempt / "stdout.txt").exists()
    assert not (attempt / "variables-v3").exists()


@pytest.mark.parametrize(
    "command",
    (
        "cat <<$producer.output\nbody\n",
        "cat <<EOF\n$producer.output\nEOF\n",
        "cat <<'EOF'\n$producer.output\nEOF\n",
        "cat <<\\EOF\n$producer.output\nEOF\n",
        "printf '%s' \"$(printf '%s' $producer.output)\"",
        "printf '%s' `printf '%s' $producer.output`",
        "printf '%s' \"$((1 + $producer.output))\"",
        "(( $producer.output ))",
        "for ((i=$producer.output; i<2; i++)); do :; done",
        "printf '%s' \"${OTHER:-$producer.output}\"",
        "printf '%s' \"$producer.output",
        "printf '%s' $(printf '%s' \"$producer.output\"",
    ),
)
def test_v3_bash_rejects_unsafe_output_reference_contexts_during_admission(
    tmp_path, workflow_writer, command
) -> None:
    with pytest.raises(WorkflowValidationError) as exc:
        _archon_bash_package(
            workflow_writer,
            tmp_path,
            command,
            depends_on=("producer",),
        )

    assert [issue.code for issue in exc.value.issues] == [
        "bash_reference_context_unsupported"
    ]
    assert exc.value.issues[0].path == "nodes[1].bash"


@pytest.mark.parametrize(
    ("template", "context"),
    _PROCESS_SUBSTITUTION_CONTEXTS,
    ids=[context for _template, context in _PROCESS_SUBSTITUTION_CONTEXTS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_rejects_process_substitution_command_contexts_at_admission(
    tmp_path,
    workflow_writer,
    template,
    context,
    reference,
) -> None:
    command = template.format(reference=reference)

    with pytest.raises(WorkflowValidationError) as exc:
        _archon_bash_package(
            workflow_writer,
            tmp_path,
            command,
            depends_on=("producer",) if reference == "$producer.output" else (),
        )

    assert [issue.code for issue in exc.value.issues] == [
        "bash_reference_context_unsupported"
    ], context
    assert exc.value.issues[0].path == "nodes[1].bash"


@pytest.mark.parametrize(
    ("template", "context"),
    _UNSAFE_WORD_EXPANSION_CONTEXTS,
    ids=[context for _template, context in _UNSAFE_WORD_EXPANSION_CONTEXTS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_rejects_extglob_and_brace_expansion_contexts_at_admission(
    tmp_path,
    workflow_writer,
    template,
    context,
    reference,
) -> None:
    command = template.format(reference=reference)

    with pytest.raises(WorkflowValidationError) as exc:
        _archon_bash_package(
            workflow_writer,
            tmp_path,
            command,
            depends_on=("producer",) if reference == "$producer.output" else (),
        )

    assert [issue.code for issue in exc.value.issues] == [
        "bash_reference_context_unsupported"
    ], context


@pytest.mark.parametrize(
    "command",
    (
        "(( $USER_MESSAGE ))",
        "for ((i=$USER_MESSAGE; i<2; i++)); do :; done",
    ),
)
def test_v3_bash_rejects_bare_arithmetic_scalar_references_during_admission(
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


@pytest.mark.parametrize(
    ("template", "context"),
    _JOINED_UNSUPPORTED_CONTEXTS,
    ids=[context for _template, context in _JOINED_UNSUPPORTED_CONTEXTS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_rejects_line_continuations_that_form_unsupported_operators_at_admission(
    tmp_path,
    workflow_writer,
    template,
    context,
    reference,
) -> None:
    command = template.format(reference=reference)

    with pytest.raises(WorkflowValidationError) as exc:
        _archon_bash_package(
            workflow_writer,
            tmp_path,
            command,
            depends_on=("producer",) if reference == "$producer.output" else (),
        )

    assert [issue.code for issue in exc.value.issues] == [
        "bash_reference_context_unsupported"
    ], context
    assert exc.value.issues[0].path == "nodes[1].bash"


@pytest.mark.parametrize(
    ("template", "context"),
    _ARRAY_SUBSCRIPT_CONTEXTS,
    ids=[context for _template, context in _ARRAY_SUBSCRIPT_CONTEXTS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_rejects_arithmetic_array_subscripts_at_admission(
    tmp_path,
    workflow_writer,
    template,
    context,
    reference,
) -> None:
    command = template.format(reference=reference)

    with pytest.raises(WorkflowValidationError) as exc:
        _archon_bash_package(
            workflow_writer,
            tmp_path,
            command,
            depends_on=("producer",) if reference == "$producer.output" else (),
        )

    assert [issue.code for issue in exc.value.issues] == [
        "bash_reference_context_unsupported"
    ], context
    assert exc.value.issues[0].path == "nodes[1].bash"


@pytest.mark.parametrize(
    ("template", "context"),
    _PREFIXED_ARRAY_SUBSCRIPT_CONTEXTS,
    ids=[context for _template, context in _PREFIXED_ARRAY_SUBSCRIPT_CONTEXTS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_rejects_prefixed_arithmetic_array_subscripts_at_admission(
    tmp_path,
    workflow_writer,
    template,
    context,
    reference,
) -> None:
    command = template.format(reference=reference)

    with pytest.raises(WorkflowValidationError) as exc:
        _archon_bash_package(
            workflow_writer,
            tmp_path,
            command,
            depends_on=("producer",) if reference == "$producer.output" else (),
        )

    assert [issue.code for issue in exc.value.issues] == [
        "bash_reference_context_unsupported"
    ], context
    assert exc.value.issues[0].path == "nodes[1].bash"


@pytest.mark.parametrize("template", _AUTHORED_END_FAIL_OPEN_CONTEXTS)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_rejects_continued_declarations_and_then_case_at_admission(
    tmp_path,
    workflow_writer,
    template,
    reference,
) -> None:
    command = template.replace("{reference}", reference)

    with pytest.raises(WorkflowValidationError) as exc:
        _archon_bash_package(
            workflow_writer,
            tmp_path,
            command,
            depends_on=("producer",) if reference == "$producer.output" else (),
        )

    assert [issue.code for issue in exc.value.issues] == [
        "bash_reference_context_unsupported"
    ]
    assert exc.value.issues[0].path == "nodes[1].bash"


@pytest.mark.parametrize(
    ("template", "context"),
    (*_FD_HEREDOC_ARRAY_SUBSCRIPT_CONTEXTS, *_QUOTE_REMOVED_ARRAY_SUBSCRIPT_CONTEXTS),
    ids=[
        context
        for _template, context in (
            *_FD_HEREDOC_ARRAY_SUBSCRIPT_CONTEXTS,
            *_QUOTE_REMOVED_ARRAY_SUBSCRIPT_CONTEXTS,
        )
    ],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_rejects_dequoted_and_fd_heredoc_array_subscripts_at_admission(
    tmp_path,
    workflow_writer,
    template,
    context,
    reference,
) -> None:
    command = template.format(reference=reference)

    with pytest.raises(WorkflowValidationError) as exc:
        _archon_bash_package(
            workflow_writer,
            tmp_path,
            command,
            depends_on=("producer",) if reference == "$producer.output" else (),
        )

    assert [issue.code for issue in exc.value.issues] == [
        "bash_reference_context_unsupported"
    ], context
    assert exc.value.issues[0].path == "nodes[1].bash"


@pytest.mark.parametrize(
    ("template", "context"),
    _ESCAPED_ARRAY_SUBSCRIPT_CONTEXTS,
    ids=[context for _template, context in _ESCAPED_ARRAY_SUBSCRIPT_CONTEXTS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_ignores_escaped_array_subscript_candidates_at_admission(
    tmp_path,
    workflow_writer,
    template,
    context,
    reference,
) -> None:
    command = template.format(reference=reference)

    package = _archon_bash_package(workflow_writer, tmp_path, command)

    assert package.definition.nodes[1].depends_on == (), context
    assert package.definition.nodes[1].value == command


@pytest.mark.parametrize(
    ("delimiter", "delimiter_kind"),
    _QUOTED_HEREDOC_DELIMITERS,
    ids=[kind for _delimiter, kind in _QUOTED_HEREDOC_DELIMITERS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_preserves_literal_continuations_in_quoted_heredoc_bodies_at_admission(
    tmp_path,
    workflow_writer,
    delimiter,
    delimiter_kind,
    reference,
) -> None:
    command = f"cat <<{delimiter} >/dev/null\nline\\\nEOF\nprintf '%s' \"{reference}\""

    package = _archon_bash_package(
        workflow_writer,
        tmp_path,
        command,
        depends_on=("producer",) if reference == "$producer.output" else (),
    )

    assert package.definition.nodes[1].value == command, delimiter_kind


@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_keeps_continuation_removal_active_in_unquoted_heredoc_bodies(
    tmp_path,
    workflow_writer,
    reference,
) -> None:
    command = "cat <<EOF >/dev/null\nline\\\nEOF\nprintf '%s' " + reference

    with pytest.raises(WorkflowValidationError) as exc:
        _archon_bash_package(
            workflow_writer,
            tmp_path,
            command,
            depends_on=("producer",) if reference == "$producer.output" else (),
        )

    assert [issue.code for issue in exc.value.issues] == [
        "bash_reference_context_unsupported"
    ]


@pytest.mark.parametrize(
    ("delimiter", "delimiter_kind"),
    _QUOTED_HEREDOC_DELIMITERS,
    ids=[kind for _delimiter, kind in _QUOTED_HEREDOC_DELIMITERS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_preserves_quoted_heredoc_joined_operator_at_admission(
    tmp_path,
    workflow_writer,
    delimiter,
    delimiter_kind,
    reference,
) -> None:
    command = (
        f"cat <\\\n<{delimiter} >/dev/null\nline\\\nEOF\nprintf '%s' \"{reference}\""
    )

    package = _archon_bash_package(
        workflow_writer,
        tmp_path,
        command,
        depends_on=("producer",) if reference == "$producer.output" else (),
    )

    assert package.definition.nodes[1].value == command, delimiter_kind


@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_does_not_treat_a_split_unquoted_delimiter_as_quoted(
    tmp_path,
    workflow_writer,
    reference,
) -> None:
    command = "cat <<EO\\\nF >/dev/null\nline\\\nEOF\nprintf '%s' " + reference

    with pytest.raises(WorkflowValidationError) as exc:
        _archon_bash_package(
            workflow_writer,
            tmp_path,
            command,
            depends_on=("producer",) if reference == "$producer.output" else (),
        )

    assert [issue.code for issue in exc.value.issues] == [
        "bash_reference_context_unsupported"
    ]


@pytest.mark.parametrize(
    ("template", "context"),
    _JOINED_HEREDOC_CONTEXTS,
    ids=[context for _template, context in _JOINED_HEREDOC_CONTEXTS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_parses_logically_joined_heredocs_at_every_operator_boundary(
    tmp_path,
    workflow_writer,
    template,
    context,
    reference,
) -> None:
    command = template.format(reference=reference)

    package = _archon_bash_package(
        workflow_writer,
        tmp_path,
        command,
        depends_on=("producer",) if reference == "$producer.output" else (),
    )

    assert package.definition.nodes[1].value == command, context


@pytest.mark.parametrize(
    ("delimiter", "delimiter_kind"),
    _COMMENT_FOLLOWED_HEREDOC_DELIMITERS,
    ids=[kind for _delimiter, kind in _COMMENT_FOLLOWED_HEREDOC_DELIMITERS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_physical_comment_newline_exposes_following_real_heredoc_at_admission(
    tmp_path,
    workflow_writer,
    delimiter,
    delimiter_kind,
    reference,
) -> None:
    command = f"# physical comment \\\n<<{delimiter}\n{reference}\nEOF\n"

    with pytest.raises(WorkflowValidationError) as exc:
        _archon_bash_package(
            workflow_writer,
            tmp_path,
            command,
            depends_on=("producer",) if reference == "$producer.output" else (),
        )

    assert [issue.code for issue in exc.value.issues] == [
        "bash_reference_context_unsupported"
    ], delimiter_kind


@pytest.mark.parametrize(
    ("template", "context"),
    _PRIOR_CONTINUATION_COMMENT_HEREDOC_CONTEXTS,
    ids=[
        context for _template, context in _PRIOR_CONTINUATION_COMMENT_HEREDOC_CONTEXTS
    ],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_prior_continuation_comment_exposes_following_real_heredoc_at_admission(
    tmp_path,
    workflow_writer,
    template,
    context,
    reference,
) -> None:
    command = template.format(reference=reference)

    with pytest.raises(WorkflowValidationError) as exc:
        _archon_bash_package(
            workflow_writer,
            tmp_path,
            command,
            depends_on=("producer",) if reference == "$producer.output" else (),
        )

    assert [issue.code for issue in exc.value.issues] == [
        "bash_reference_context_unsupported"
    ], context


@pytest.mark.parametrize(
    ("template", "context"),
    _PHASE_REENTRY_SAFE_CONTEXTS,
    ids=[context for _template, context in _PHASE_REENTRY_SAFE_CONTEXTS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_phase_reentry_does_not_invent_or_miss_quoted_heredocs_at_admission(
    tmp_path,
    workflow_writer,
    template,
    context,
    reference,
) -> None:
    command = template.format(reference=reference)

    package = _archon_bash_package(
        workflow_writer,
        tmp_path,
        command,
        depends_on=("producer",) if reference == "$producer.output" else (),
    )

    assert package.definition.nodes[1].value == command, context


@pytest.mark.parametrize(
    "command",
    (
        "printf '%s' '<($USER_MESSAGE)'",
        "printf '%s' \"<($USER_MESSAGE)\"",
        "printf '%s' '>($USER_MESSAGE)'",
        "cat </dev/null; printf '%s' \"$USER_MESSAGE\"",
        "printf '%s' \"$USER_MESSAGE\" 2>/dev/null",
    ),
)
def test_v3_bash_keeps_quoted_process_text_and_ordinary_redirections_admitted(
    tmp_path,
    workflow_writer,
    command,
) -> None:
    package = _archon_bash_package(workflow_writer, tmp_path, command)

    assert package.definition.nodes[1].value == command


@pytest.mark.parametrize(
    ("template", "context"),
    _HERE_STRING_CONTEXTS,
    ids=[context for _template, context in _HERE_STRING_CONTEXTS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_maximal_munch_admits_here_string_operands(
    tmp_path,
    workflow_writer,
    template,
    context,
    reference,
) -> None:
    command = template.format(reference=reference)

    package = _archon_bash_package(
        workflow_writer,
        tmp_path,
        command,
        depends_on=("producer",) if reference == "$producer.output" else (),
    )

    assert package.definition.nodes[1].value == command, context


@pytest.mark.parametrize(
    "command",
    (
        "printf '%s' '@($USER_MESSAGE|x)'",
        "printf '%s' \"!($USER_MESSAGE)\"",
        "printf '%s' 'pre{$USER_MESSAGE,x}post'",
        "printf '%s' \"pre{x,$USER_MESSAGE}post\"",
    ),
)
def test_v3_bash_keeps_quoted_extglob_and_brace_text_admitted(
    tmp_path,
    workflow_writer,
    command,
) -> None:
    package = _archon_bash_package(workflow_writer, tmp_path, command)

    assert package.definition.nodes[1].value == command


@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_prior_continuation_comment_ends_before_following_safe_reference(
    tmp_path,
    workflow_writer,
    reference,
) -> None:
    command = f": \\\n# comment \\\nprintf '%s' \"{reference}\""

    package = _archon_bash_package(
        workflow_writer,
        tmp_path,
        command,
        depends_on=("producer",) if reference == "$producer.output" else (),
    )

    assert package.definition.nodes[1].value == command


def test_v3_bash_admits_quoted_scalar_reference_as_simple_token_data(
    tmp_path,
    workflow_writer,
) -> None:
    package = _archon_bash_package(
        workflow_writer,
        tmp_path,
        "printf '%s' \"$USER_MESSAGE\"",
    )

    assert package.definition.nodes[1].value == "printf '%s' \"$USER_MESSAGE\""


@pytest.mark.parametrize(
    "command",
    (
        "printf '%s' \"items[$USER_MESSAGE]=9\"",
        "printf '%s' \"items=([$USER_MESSAGE]=9)\"",
        "printf '%s' items[$USER_MESSAGE]=9",
        "command printf '%s' items[$USER_MESSAGE]=9",
        "builtin printf '%s' items[$USER_MESSAGE]=9",
        "printf '%s' [[ $USER_MESSAGE ]]",
        ("items=([0]=prefix[$USER_MESSAGE]=9); printf '%s' \"${items[0]}\""),
        "(\\\n( 1 )); printf '%s' \"$USER_MESSAGE\"",
    ),
    ids=(
        "quoted-index",
        "quoted-compound-index",
        "argument-index-text",
        "command-argument-index-text",
        "builtin-argument-index-text",
        "argument-conditional-text",
        "compound-value-bracket-text",
        "later-safe-reference",
    ),
)
def test_v3_bash_keeps_quoted_brackets_and_unrelated_line_continuations_compatible(
    tmp_path,
    workflow_writer,
    command,
) -> None:
    package = _archon_bash_package(workflow_writer, tmp_path, command)

    assert package.definition.nodes[1].value == command


@pytest.mark.parametrize(
    ("command", "context"),
    _FALSE_HEREDOC_TOKEN_CONTEXTS,
    ids=[context for _command, context in _FALSE_HEREDOC_TOKEN_CONTEXTS],
)
def test_v3_bash_does_not_invent_heredocs_from_quoted_or_nested_tokens_at_admission(
    tmp_path,
    workflow_writer,
    command,
    context,
) -> None:
    package = _archon_bash_package(workflow_writer, tmp_path, command)

    assert package.definition.nodes[1].value == command, context


def test_v3_bash_ends_a_physical_comment_before_the_following_safe_reference(
    tmp_path,
    workflow_writer,
) -> None:
    command = "# physical comment \\\nprintf '%s' \"$producer.output\""

    package = _archon_bash_package(
        workflow_writer,
        tmp_path,
        command,
        depends_on=("producer",),
    )

    assert package.definition.nodes[1].depends_on == ("producer",)


def test_v3_bash_context_rejection_precedes_dependency_validation(
    tmp_path,
    workflow_writer,
) -> None:
    with pytest.raises(WorkflowValidationError) as exc:
        _archon_bash_package(
            workflow_writer,
            tmp_path,
            "printf '%s' \"$(printf $producer.output)\"",
        )

    assert [issue.code for issue in exc.value.issues] == [
        "bash_reference_context_unsupported"
    ]


@pytest.mark.parametrize(
    "command",
    (
        "printf '%s' \\$producer.output",
        "# $producer.output\nprintf safe",
        "\\\n# $producer.output\nprintf safe",
        "printf '%s' \"$( # $producer.output\nprintf safe)\"",
    ),
)
def test_v3_bash_ignores_escaped_and_comment_output_references_at_admission(
    tmp_path, workflow_writer, command
) -> None:
    package = _archon_bash_package(workflow_writer, tmp_path, command)

    assert package.definition.nodes[-1].depends_on == ()


@pytest.mark.parametrize(
    ("command", "expected"),
    (
        ("printf '%s' \\$producer.output", b"$producer.output"),
        ("# $producer.output\nprintf safe", b"safe"),
    ),
)
def test_v3_bash_ignores_literal_output_references_during_execution(
    tmp_path,
    command,
    expected,
) -> None:
    node = WorkflowNode(
        id="shell",
        node_type="bash",
        value=command,
        depends_on=(),
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )

    result = BashExecutor().execute(
        NodeExecutionContext(
            run_id="literal-reference",
            run_directory=tmp_path,
            node=node,
            attempt_id="attempt-1",
            variable_context=VariableContext(normalizer_version=3),
            language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=3,
        )
    )

    assert result.status == "succeeded"
    assert (
        tmp_path / "nodes" / "shell" / "attempt-1" / "stdout.txt"
    ).read_bytes() == expected


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
@pytest.mark.parametrize("byte_count", [32_767, 32_768, 32_769])
@pytest.mark.parametrize(
    ("context", "command"),
    tuple(_SHELL_CONTEXTS.items()),
)
def test_v3_bash_preserves_exact_bytes_across_inline_and_spill_boundary(
    tmp_path,
    byte_count,
    context,
    command,
) -> None:
    value = "a" * byte_count

    result, output = _run_v3_bash(tmp_path, command=command, value=value)

    assert result.status == "succeeded", context
    assert output == value.encode("utf-8"), context
    spill_root = tmp_path / "nodes" / "shell" / "attempt-1" / "variables-v3"
    assert not spill_root.exists()
    assert result.metadata["bash"]["spill_count"] == int(byte_count > 32_768)


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
@pytest.mark.parametrize(
    "value",
    [
        "",
        "space 'single' \"double\" $dollar `backtick` * ? [glob] café x\n\n",
        (
            "space 'single' \"double\" $dollar `backtick` * ? [glob] café "
            + "z" * 32_769
            + "x\n\n"
        ),
    ],
)
@pytest.mark.parametrize(
    ("context", "command"),
    tuple(_SHELL_CONTEXTS.items()),
)
def test_v3_bash_treats_inline_and_spilled_metacharacters_as_exact_data(
    tmp_path,
    value,
    context,
    command,
) -> None:
    (tmp_path / "glob-target").write_text("must not expand", encoding="utf-8")

    result, output = _run_v3_bash(tmp_path, command=command, value=value)

    assert result.status == "succeeded", context
    assert output == value.encode("utf-8"), context


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
@pytest.mark.parametrize(
    ("value", "expected_bytes"),
    [
        ("a" * 32_766 + "é", 32_768),
        ("a" * 32_767 + "é", 32_769),
    ],
)
@pytest.mark.parametrize("command", tuple(_SHELL_CONTEXTS.values()))
def test_v3_bash_uses_utf8_bytes_for_multibyte_inline_boundary(
    tmp_path,
    value,
    expected_bytes,
    command,
) -> None:
    assert len(value.encode("utf-8")) == expected_bytes

    result, output = _run_v3_bash(tmp_path, command=command, value=value)

    assert result.status == "succeeded"
    assert output == value.encode("utf-8")


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
def test_v3_spill_expansion_stays_inside_the_original_double_quoted_word(
    tmp_path,
) -> None:
    value = "space * ' quote " + "z" * 32_769 + "x\n\n"

    result, output = _run_v3_bash(
        tmp_path,
        command='printf "%s" "prefix-$USER_MESSAGE-suffix"',
        value=value,
    )

    assert result.status == "succeeded"
    assert output == f"prefix-{value}-suffix".encode("utf-8")


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
@pytest.mark.parametrize(
    "command",
    [
        "cat <<$USER_MESSAGE\nbody\n",
        "cat <<EOF\n$USER_MESSAGE\nEOF\n",
        "cat <<'EOF'\n$USER_MESSAGE\nEOF\n",
        "cat <<\\EOF\n$USER_MESSAGE\nEOF\n",
        "printf '%s' \"$(printf '%s' $USER_MESSAGE)\"",
        "printf '%s' `printf '%s' $USER_MESSAGE`",
        "printf '%s' \"$((1 + $USER_MESSAGE))\"",
        "printf '%s' \"${OTHER:-$USER_MESSAGE}\"",
        "printf '%s' \"$USER_MESSAGE",
        "printf '%s' '$USER_MESSAGE",
        "printf '%s' $(printf '%s' \"$USER_MESSAGE\"",
    ],
)
def test_v3_bash_rejects_references_in_unsupported_shell_contexts_before_launch(
    tmp_path,
    command,
) -> None:
    launched: list[str] = []

    result, output = _run_v3_bash(
        tmp_path,
        command=command,
        value="$(touch must-not-run)",
        spawn_intent=lambda nonce: launched.append(nonce) or True,
    )

    assert result.status == "failed"
    assert result.error_code == "bash_reference_context_unsupported"
    assert launched == []
    assert output is None
    assert not (tmp_path / "must-not-run").exists()


@pytest.mark.parametrize(
    ("template", "context"),
    _PROCESS_SUBSTITUTION_CONTEXTS,
    ids=[context for _template, context in _PROCESS_SUBSTITUTION_CONTEXTS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_bash_rejects_process_substitution_before_resolution_spill_or_launch(
    tmp_path,
    template,
    context,
    reference,
    size,
) -> None:
    _execute_rejected_bash_context(
        tmp_path / context / reference.removeprefix("$"),
        command=template.format(reference=reference),
        reference=reference,
        size=size,
    )


@pytest.mark.parametrize(
    ("template", "context"),
    _UNSAFE_WORD_EXPANSION_CONTEXTS,
    ids=[context for _template, context in _UNSAFE_WORD_EXPANSION_CONTEXTS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_bash_rejects_extglob_and_brace_expansion_before_side_effects(
    tmp_path,
    template,
    context,
    reference,
    size,
) -> None:
    _execute_rejected_bash_context(
        tmp_path / context / reference.removeprefix("$"),
        command=template.format(reference=reference),
        reference=reference,
        size=size,
    )


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
@pytest.mark.parametrize(
    ("template", "reference"),
    (
        ("(( {reference} ))", "$USER_MESSAGE"),
        ("for ((i={reference}; i<2; i++)); do :; done", "$USER_MESSAGE"),
        ("(( {reference} ))", "$producer.output"),
        ("for ((i={reference}; i<2; i++)); do :; done", "$producer.output"),
    ),
    ids=("scalar-command", "scalar-for", "output-command", "output-for"),
)
@pytest.mark.parametrize(
    "value",
    ("1", "1" * 32_769),
    ids=("inline-sized", "spill-sized"),
)
def test_v3_bash_rejects_bare_arithmetic_references_before_executor_launch(
    tmp_path,
    template,
    reference,
    value,
) -> None:
    command = template.format(reference=reference)
    output_reference = reference == "$producer.output"
    dependencies = ("producer",) if output_reference else ()
    variables = VariableContext(
        user_message=value,
        normalizer_version=3,
    )
    renderer = substitution_renderer(
        variables,
        direct_dependencies=dependencies,
        output_resolver=(
            (lambda _node_id, _path: ResolvedOutputReference(value, value))
            if output_reference
            else None
        ),
    )
    launched: list[str] = []
    node = WorkflowNode(
        id="shell",
        node_type="bash",
        value=command,
        depends_on=dependencies,
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )

    result = BashExecutor().execute(
        NodeExecutionContext(
            run_id="bare-arithmetic",
            run_directory=tmp_path,
            node=node,
            attempt_id="attempt-1",
            variable_context=renderer,
            language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=3,
            spawn_intent=lambda nonce: launched.append(nonce) or True,
        )
    )

    assert result.status == "failed"
    assert result.error_code == "bash_reference_context_unsupported"
    assert launched == []
    assert not (tmp_path / "nodes" / "shell" / "attempt-1" / "stdout.txt").exists()


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
@pytest.mark.parametrize(
    ("template", "context"),
    (*_JOINED_UNSUPPORTED_CONTEXTS, *_ARRAY_SUBSCRIPT_CONTEXTS),
    ids=[
        context
        for _template, context in (
            *_JOINED_UNSUPPORTED_CONTEXTS,
            *_ARRAY_SUBSCRIPT_CONTEXTS,
        )
    ],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_bash_rejects_joined_operators_and_array_subscripts_before_launch(
    tmp_path,
    template,
    context,
    reference,
    size,
) -> None:
    value = "7" * size
    output_reference = reference == "$producer.output"
    dependencies = ("producer",) if output_reference else ()
    renderer = substitution_renderer(
        VariableContext(user_message=value, normalizer_version=3),
        direct_dependencies=dependencies,
        output_resolver=(
            (lambda _node_id, _path: ResolvedOutputReference(value, value))
            if output_reference
            else None
        ),
    )
    launched: list[str] = []
    node = WorkflowNode(
        id="shell",
        node_type="bash",
        value=template.format(reference=reference),
        depends_on=dependencies,
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )

    result = BashExecutor().execute(
        NodeExecutionContext(
            run_id=f"unsupported-{context}",
            run_directory=tmp_path,
            node=node,
            attempt_id="attempt-1",
            variable_context=renderer,
            language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=3,
            spawn_intent=lambda nonce: launched.append(nonce) or True,
        )
    )

    assert result.status == "failed", context
    assert result.error_code == "bash_reference_context_unsupported", context
    assert launched == [], context
    attempt = tmp_path / "nodes" / "shell" / "attempt-1"
    assert not (attempt / "stdout.txt").exists(), context
    assert not (attempt / "variables-v3").exists(), context


@pytest.mark.parametrize(
    ("template", "context"),
    _PREFIXED_ARRAY_SUBSCRIPT_CONTEXTS,
    ids=[context for _template, context in _PREFIXED_ARRAY_SUBSCRIPT_CONTEXTS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_bash_rejects_prefixed_array_subscripts_before_launch(
    tmp_path,
    template,
    context,
    reference,
    size,
) -> None:
    value = "7" * size
    output_reference = reference == "$producer.output"
    dependencies = ("producer",) if output_reference else ()
    renderer = substitution_renderer(
        VariableContext(user_message=value, normalizer_version=3),
        direct_dependencies=dependencies,
        output_resolver=(
            (lambda _node_id, _path: ResolvedOutputReference(value, value))
            if output_reference
            else None
        ),
    )
    launched: list[str] = []
    node = WorkflowNode(
        id="shell",
        node_type="bash",
        value=template.format(reference=reference),
        depends_on=dependencies,
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )

    result = BashExecutor().execute(
        NodeExecutionContext(
            run_id=f"prefixed-array-{context}",
            run_directory=tmp_path,
            node=node,
            attempt_id="attempt-1",
            variable_context=renderer,
            language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=3,
            spawn_intent=lambda nonce: launched.append(nonce) or True,
        )
    )

    assert result.status == "failed", context
    assert result.error_code == "bash_reference_context_unsupported", context
    assert launched == [], context
    attempt = tmp_path / "nodes" / "shell" / "attempt-1"
    assert not (attempt / "stdout.txt").exists(), context
    assert not (attempt / "variables-v3").exists(), context


@pytest.mark.parametrize("template", _AUTHORED_END_FAIL_OPEN_CONTEXTS)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_bash_rejects_continued_declarations_and_then_case_before_side_effects(
    tmp_path,
    template,
    reference,
    size,
) -> None:
    _execute_rejected_bash_context(
        tmp_path / reference.removeprefix("$"),
        command=template.replace("{reference}", reference),
        reference=reference,
        size=size,
    )


@pytest.mark.parametrize(
    ("template", "context"),
    (*_FD_HEREDOC_ARRAY_SUBSCRIPT_CONTEXTS, *_QUOTE_REMOVED_ARRAY_SUBSCRIPT_CONTEXTS),
    ids=[
        context
        for _template, context in (
            *_FD_HEREDOC_ARRAY_SUBSCRIPT_CONTEXTS,
            *_QUOTE_REMOVED_ARRAY_SUBSCRIPT_CONTEXTS,
        )
    ],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_bash_rejects_dequoted_and_fd_heredoc_subscripts_before_resolution_or_launch(
    tmp_path,
    template,
    context,
    reference,
    size,
) -> None:
    _execute_rejected_bash_context(
        tmp_path / context / reference.removeprefix("$"),
        command=template.format(reference=reference),
        reference=reference,
        size=size,
    )


@pytest.mark.parametrize(
    ("template", "context"),
    _ESCAPED_ARRAY_SUBSCRIPT_CONTEXTS,
    ids=[context for _template, context in _ESCAPED_ARRAY_SUBSCRIPT_CONTEXTS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_keeps_escaped_array_subscript_candidates_literal_without_spilling(
    tmp_path,
    template,
    context,
    reference,
) -> None:
    resolver = MagicMock(side_effect=AssertionError("literal output was resolved"))
    renderer = substitution_renderer(
        VariableContext(user_message="7" * 32_769, normalizer_version=3),
        direct_dependencies=(),
        output_resolver=resolver,
    )
    command = template.format(reference=reference)
    spill_directory = tmp_path / context / "variables-v3"

    rendered = renderer.render_bash(
        command,
        spill_directory=spill_directory,
        secure_v3=True,
    )

    try:
        assert rendered.command == command, context
        assert rendered.spill_count == 0, context
        assert rendered.inherited_descriptors == (), context
        assert not spill_directory.exists(), context
        resolver.assert_not_called()
    finally:
        rendered.close()


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
@pytest.mark.parametrize(
    ("delimiter", "delimiter_kind"),
    _QUOTED_HEREDOC_DELIMITERS,
    ids=[kind for _delimiter, kind in _QUOTED_HEREDOC_DELIMITERS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_bash_preserves_quoted_heredoc_body_continuations_during_execution(
    tmp_path,
    delimiter,
    delimiter_kind,
    reference,
    size,
) -> None:
    value = "safe-" + "7" * size
    output_reference = reference == "$producer.output"
    dependencies = ("producer",) if output_reference else ()
    renderer = substitution_renderer(
        VariableContext(user_message=value, normalizer_version=3),
        direct_dependencies=dependencies,
        output_resolver=(
            (lambda _node_id, _path: ResolvedOutputReference(value, value))
            if output_reference
            else None
        ),
    )
    command = f"cat <<{delimiter} >/dev/null\nline\\\nEOF\nprintf '%s' \"{reference}\""
    node = WorkflowNode(
        id="shell",
        node_type="bash",
        value=command,
        depends_on=dependencies,
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )

    result = BashExecutor().execute(
        NodeExecutionContext(
            run_id=f"quoted-heredoc-{delimiter_kind}",
            run_directory=tmp_path,
            node=node,
            attempt_id="attempt-1",
            variable_context=renderer,
            language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=3,
        )
    )

    assert result.status == "succeeded", delimiter_kind
    assert (tmp_path / "nodes" / "shell" / "attempt-1" / "stdout.txt").read_text(
        encoding="utf-8"
    ) == value
    assert result.metadata["bash"]["spill_count"] == (size > 32_768)


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
@pytest.mark.parametrize(
    ("delimiter", "delimiter_kind"),
    _QUOTED_HEREDOC_DELIMITERS,
    ids=[kind for _delimiter, kind in _QUOTED_HEREDOC_DELIMITERS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_bash_preserves_quoted_heredoc_body_after_joined_operator(
    tmp_path,
    delimiter,
    delimiter_kind,
    reference,
    size,
) -> None:
    value = "safe-" + "7" * size
    output_reference = reference == "$producer.output"
    dependencies = ("producer",) if output_reference else ()
    renderer = substitution_renderer(
        VariableContext(user_message=value, normalizer_version=3),
        direct_dependencies=dependencies,
        output_resolver=(
            (lambda _node_id, _path: ResolvedOutputReference(value, value))
            if output_reference
            else None
        ),
    )
    command = (
        f"cat <\\\n<{delimiter} >/dev/null\nline\\\nEOF\nprintf '%s' \"{reference}\""
    )
    node = WorkflowNode(
        id="shell",
        node_type="bash",
        value=command,
        depends_on=dependencies,
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )

    result = BashExecutor().execute(
        NodeExecutionContext(
            run_id=f"joined-quoted-heredoc-{delimiter_kind}",
            run_directory=tmp_path,
            node=node,
            attempt_id="attempt-1",
            variable_context=renderer,
            language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=3,
        )
    )

    assert result.status == "succeeded", delimiter_kind
    assert (tmp_path / "nodes" / "shell" / "attempt-1" / "stdout.txt").read_text(
        encoding="utf-8"
    ) == value
    assert result.metadata["bash"]["spill_count"] == (size > 32_768)


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
@pytest.mark.parametrize(
    ("template", "context"),
    _JOINED_HEREDOC_CONTEXTS,
    ids=[context for _template, context in _JOINED_HEREDOC_CONTEXTS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_bash_executes_logically_joined_heredocs_with_exact_following_reference(
    tmp_path,
    template,
    context,
    reference,
    size,
) -> None:
    value = "safe-" + "7" * size
    output_reference = reference == "$producer.output"
    dependencies = ("producer",) if output_reference else ()
    renderer = substitution_renderer(
        VariableContext(user_message=value, normalizer_version=3),
        direct_dependencies=dependencies,
        output_resolver=(
            (lambda _node_id, _path: ResolvedOutputReference(value, value))
            if output_reference
            else None
        ),
    )
    node = WorkflowNode(
        id="shell",
        node_type="bash",
        value=template.format(reference=reference),
        depends_on=dependencies,
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )

    result = BashExecutor().execute(
        NodeExecutionContext(
            run_id=f"joined-heredoc-{context}",
            run_directory=tmp_path,
            node=node,
            attempt_id="attempt-1",
            variable_context=renderer,
            language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=3,
        )
    )

    assert result.status == "succeeded", context
    assert (tmp_path / "nodes" / "shell" / "attempt-1" / "stdout.txt").read_text(
        encoding="utf-8"
    ) == value
    assert result.metadata["bash"]["spill_count"] == (size > 32_768)


@pytest.mark.parametrize(
    ("delimiter", "delimiter_kind"),
    _COMMENT_FOLLOWED_HEREDOC_DELIMITERS,
    ids=[kind for _delimiter, kind in _COMMENT_FOLLOWED_HEREDOC_DELIMITERS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_bash_rejects_a_real_heredoc_after_a_physical_comment_before_side_effects(
    tmp_path,
    delimiter,
    delimiter_kind,
    reference,
    size,
) -> None:
    _execute_rejected_bash_context(
        tmp_path / delimiter_kind / reference.removeprefix("$"),
        command=f"# physical comment \\\n<<{delimiter}\n{reference}\nEOF\n",
        reference=reference,
        size=size,
    )


@pytest.mark.parametrize(
    ("template", "context"),
    _PRIOR_CONTINUATION_COMMENT_HEREDOC_CONTEXTS,
    ids=[
        context for _template, context in _PRIOR_CONTINUATION_COMMENT_HEREDOC_CONTEXTS
    ],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_bash_prior_continuation_comment_rejects_heredoc_before_side_effects(
    tmp_path,
    template,
    context,
    reference,
    size,
) -> None:
    _execute_rejected_bash_context(
        tmp_path / context / reference.removeprefix("$"),
        command=template.format(reference=reference),
        reference=reference,
        size=size,
    )


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
@pytest.mark.parametrize(
    ("template", "context"),
    _PHASE_REENTRY_SAFE_CONTEXTS,
    ids=[context for _template, context in _PHASE_REENTRY_SAFE_CONTEXTS],
)
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_bash_executes_safe_reference_after_phase_reentry_constructs(
    tmp_path,
    template,
    context,
    size,
) -> None:
    value = "safe-" + "7" * size

    result, output = _run_v3_bash(
        tmp_path,
        command=template.format(reference="$USER_MESSAGE"),
        value=value,
    )

    assert result.status == "succeeded", context
    assert output is not None and output.endswith(value.encode("utf-8")), context
    assert result.metadata["bash"]["spill_count"] == (size > 32_768)


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_bash_executes_reference_after_prior_continuation_comment(
    tmp_path,
    size,
) -> None:
    value = "safe-" + "7" * size

    result, output = _run_v3_bash(
        tmp_path,
        command=": \\\n# comment \\\nprintf '%s' \"$USER_MESSAGE\"",
        value=value,
    )

    assert result.status == "succeeded"
    assert output == value.encode("utf-8")
    assert result.metadata["bash"]["spill_count"] == (size > 32_768)


@pytest.mark.skipif(os.name == "nt", reason="real Bash here-string contract")
@pytest.mark.parametrize(
    ("template", "context"),
    _HERE_STRING_CONTEXTS,
    ids=[context for _template, context in _HERE_STRING_CONTEXTS],
)
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_bash_executes_here_string_operand_as_one_data_value(
    tmp_path,
    template,
    context,
    size,
) -> None:
    value = "safe value " + "7" * size

    result, output = _run_v3_bash(
        tmp_path,
        command=template.format(reference="$USER_MESSAGE"),
        value=value,
    )

    assert result.status == "succeeded", context
    assert output == value.encode("utf-8"), context
    assert result.metadata["bash"]["spill_count"] == (size > 32_768)


@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_bash_rejects_active_unquoted_heredoc_continuations_before_launch(
    tmp_path,
    reference,
    size,
) -> None:
    value = "7" * size
    output_reference = reference == "$producer.output"
    dependencies = ("producer",) if output_reference else ()
    renderer = substitution_renderer(
        VariableContext(user_message=value, normalizer_version=3),
        direct_dependencies=dependencies,
        output_resolver=(
            (lambda _node_id, _path: ResolvedOutputReference(value, value))
            if output_reference
            else None
        ),
    )
    launched: list[str] = []
    node = WorkflowNode(
        id="shell",
        node_type="bash",
        value="cat <<EOF >/dev/null\nline\\\nEOF\nprintf '%s' " + reference,
        depends_on=dependencies,
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )

    result = BashExecutor().execute(
        NodeExecutionContext(
            run_id="unquoted-heredoc-continuation",
            run_directory=tmp_path,
            node=node,
            attempt_id="attempt-1",
            variable_context=renderer,
            language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=3,
            spawn_intent=lambda nonce: launched.append(nonce) or True,
        )
    )

    assert result.status == "failed"
    assert result.error_code == "bash_reference_context_unsupported"
    assert launched == []
    attempt = tmp_path / "nodes" / "shell" / "attempt-1"
    assert not (attempt / "stdout.txt").exists()
    assert not (attempt / "variables-v3").exists()


@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_bash_rejects_split_unquoted_delimiter_continuations_before_launch(
    tmp_path,
    reference,
    size,
) -> None:
    value = "7" * size
    output_reference = reference == "$producer.output"
    dependencies = ("producer",) if output_reference else ()
    renderer = substitution_renderer(
        VariableContext(user_message=value, normalizer_version=3),
        direct_dependencies=dependencies,
        output_resolver=(
            (lambda _node_id, _path: ResolvedOutputReference(value, value))
            if output_reference
            else None
        ),
    )
    launched: list[str] = []
    node = WorkflowNode(
        id="shell",
        node_type="bash",
        value="cat <<EO\\\nF >/dev/null\nline\\\nEOF\nprintf '%s' " + reference,
        depends_on=dependencies,
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )

    result = BashExecutor().execute(
        NodeExecutionContext(
            run_id="split-unquoted-heredoc-delimiter",
            run_directory=tmp_path,
            node=node,
            attempt_id="attempt-1",
            variable_context=renderer,
            language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=3,
            spawn_intent=lambda nonce: launched.append(nonce) or True,
        )
    )

    assert result.status == "failed"
    assert result.error_code == "bash_reference_context_unsupported"
    assert launched == []
    attempt = tmp_path / "nodes" / "shell" / "attempt-1"
    assert not (attempt / "stdout.txt").exists()
    assert not (attempt / "variables-v3").exists()


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_ends_a_physical_comment_before_rendering_a_following_safe_reference(
    tmp_path,
    reference,
) -> None:
    value = "safe-" + "7" * 32_769
    output_reference = reference == "$producer.output"
    dependencies = ("producer",) if output_reference else ()
    renderer = substitution_renderer(
        VariableContext(user_message=value, normalizer_version=3),
        direct_dependencies=dependencies,
        output_resolver=(
            (lambda _node_id, _path: ResolvedOutputReference(value, value))
            if output_reference
            else None
        ),
    )
    command = (
        "# false <<-'EOF' and literal backslash \\\nprintf '%s' \"" + reference + '"'
    )
    node = WorkflowNode(
        id="shell",
        node_type="bash",
        value=command,
        depends_on=dependencies,
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )

    result = BashExecutor().execute(
        NodeExecutionContext(
            run_id="physical-comment-safe-reference",
            run_directory=tmp_path,
            node=node,
            attempt_id="attempt-1",
            variable_context=renderer,
            language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=3,
        )
    )

    assert result.status == "succeeded"
    assert (tmp_path / "nodes" / "shell" / "attempt-1" / "stdout.txt").read_text(
        encoding="utf-8"
    ) == value
    assert result.metadata["bash"]["spill_count"] == 1


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
def test_v3_bash_does_not_substitute_a_reference_in_physical_comment_text(
    tmp_path,
) -> None:
    value = "7" * 32_769

    result, output = _run_v3_bash(
        tmp_path,
        command="# comment reference $USER_MESSAGE and backslash \\\nprintf safe",
        value=value,
    )

    assert result.status == "succeeded"
    assert output == b"safe"
    assert result.metadata["bash"]["spill_count"] == 0


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
def test_v3_bash_ignores_an_escaped_reference_after_a_line_continuation(
    tmp_path,
) -> None:
    result, output = _run_v3_bash(
        tmp_path,
        command="printf '%s' \\\n\\$USER_MESSAGE",
        value="7" * 32_769,
    )

    assert result.status == "succeeded"
    assert output == b"$USER_MESSAGE"
    assert result.metadata["bash"]["spill_count"] == 0


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
@pytest.mark.parametrize(
    ("command", "expected_template"),
    (
        ("printf '%s' \"items[$USER_MESSAGE]=9\"", "items[{value}]=9"),
        (
            "printf '%s' \"items=([$USER_MESSAGE]=9)\"",
            "items=([{value}]=9)",
        ),
        ("printf '%s' \"(\\\n( $USER_MESSAGE ))\"", "(( {value} ))"),
        ("printf '%s' items[$USER_MESSAGE]=9", "items[{value}]=9"),
        ("command printf '%s' items[$USER_MESSAGE]=9", "items[{value}]=9"),
        ("builtin printf '%s' items[$USER_MESSAGE]=9", "items[{value}]=9"),
        ("printf '%s' [[ $USER_MESSAGE ]]", "[[{value}]]"),
        (
            "items=([0]=prefix[$USER_MESSAGE]=9); printf '%s' \"${items[0]}\"",
            "prefix[{value}]=9",
        ),
    ),
    ids=(
        "quoted-index",
        "quoted-compound-index",
        "quoted-joined-operator",
        "argument-index-text",
        "command-argument-index-text",
        "builtin-argument-index-text",
        "argument-conditional-text",
        "compound-value-bracket-text",
    ),
)
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_bash_keeps_quoted_operator_and_bracket_text_as_exact_data(
    tmp_path,
    command,
    expected_template,
    size,
) -> None:
    value = "7" * size

    result, output = _run_v3_bash(tmp_path, command=command, value=value)

    assert result.status == "succeeded"
    assert output == expected_template.format(value=value).encode("utf-8")


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
@pytest.mark.parametrize(
    ("command", "context"),
    _FALSE_HEREDOC_TOKEN_CONTEXTS,
    ids=[context for _command, context in _FALSE_HEREDOC_TOKEN_CONTEXTS],
)
@pytest.mark.parametrize("size", (64, 32_769), ids=("inline", "spill"))
def test_v3_bash_executes_safe_references_after_false_heredoc_tokens(
    tmp_path,
    command,
    context,
    size,
) -> None:
    value = "safe-" + "7" * size

    result, output = _run_v3_bash(tmp_path, command=command, value=value)

    assert result.status == "succeeded", context
    assert output is not None and output.endswith(value.encode("utf-8")), context
    assert result.metadata["bash"]["spill_count"] == (size > 32_768)


@pytest.mark.parametrize(
    "command",
    (
        "(\\\n( $USER_MESSAGE ))",
        "items[$USER_MESSAGE]=9",
    ),
    ids=("joined-arithmetic", "array-subscript"),
)
def test_v3_scheduler_preflight_uses_the_bash_context_authority(
    tmp_path,
    monkeypatch,
    command,
) -> None:
    store = MagicMock()
    store.run_directory.return_value = tmp_path
    scheduler = object.__new__(RunScheduler)
    scheduler.store = store
    node = WorkflowNode(
        id="shell",
        node_type="bash",
        value=command,
        depends_on=(),
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )
    package = SimpleNamespace(
        language=SimpleNamespace(
            effective_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=3,
        )
    )
    monkeypatch.setattr(
        scheduler,
        "_revalidate_retained_output_resolution",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        scheduler,
        "_strict_reference_templates",
        lambda *_args, **_kwargs: (command,),
    )

    with pytest.raises(BashRenderingError) as exc:
        scheduler._preflight_strict_node_references(
            "run-1",
            node,
            package,
            {"nodes": {"shell": {"state": "ready"}}},
            sealed_resource_paths=frozenset(),
            sealed_resource_bytes={},
        )

    assert exc.value.code == "bash_reference_context_unsupported"


@pytest.mark.parametrize(
    ("template", "context"),
    _PREFIXED_ARRAY_SUBSCRIPT_CONTEXTS,
    ids=[context for _template, context in _PREFIXED_ARRAY_SUBSCRIPT_CONTEXTS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_scheduler_rejects_prefixed_array_subscript_contexts(
    tmp_path,
    monkeypatch,
    template,
    context,
    reference,
) -> None:
    command = template.format(reference=reference)

    with pytest.raises(BashRenderingError) as exc:
        _scheduler_preflight_bash_template(
            tmp_path,
            monkeypatch,
            command,
            depends_on=("producer",) if reference == "$producer.output" else (),
        )

    assert exc.value.code == "bash_reference_context_unsupported", context


@pytest.mark.parametrize("template", _AUTHORED_END_FAIL_OPEN_CONTEXTS)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_scheduler_rejects_continued_declarations_and_then_case(
    tmp_path,
    monkeypatch,
    template,
    reference,
) -> None:
    command = template.replace("{reference}", reference)

    with pytest.raises(BashRenderingError) as exc:
        _scheduler_preflight_bash_template(
            tmp_path,
            monkeypatch,
            command,
            depends_on=("producer",) if reference == "$producer.output" else (),
        )

    assert exc.value.code == "bash_reference_context_unsupported"


@pytest.mark.parametrize(
    ("template", "context"),
    (*_FD_HEREDOC_ARRAY_SUBSCRIPT_CONTEXTS, *_QUOTE_REMOVED_ARRAY_SUBSCRIPT_CONTEXTS),
    ids=[
        context
        for _template, context in (
            *_FD_HEREDOC_ARRAY_SUBSCRIPT_CONTEXTS,
            *_QUOTE_REMOVED_ARRAY_SUBSCRIPT_CONTEXTS,
        )
    ],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_scheduler_rejects_dequoted_and_fd_heredoc_array_subscripts(
    tmp_path,
    monkeypatch,
    template,
    context,
    reference,
) -> None:
    command = template.format(reference=reference)

    with pytest.raises(BashRenderingError) as exc:
        _scheduler_preflight_bash_template(
            tmp_path,
            monkeypatch,
            command,
            depends_on=("producer",) if reference == "$producer.output" else (),
        )

    assert exc.value.code == "bash_reference_context_unsupported", context


@pytest.mark.parametrize(
    ("template", "context"),
    _ESCAPED_ARRAY_SUBSCRIPT_CONTEXTS,
    ids=[context for _template, context in _ESCAPED_ARRAY_SUBSCRIPT_CONTEXTS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_scheduler_ignores_escaped_array_subscript_candidates(
    tmp_path,
    monkeypatch,
    template,
    context,
    reference,
) -> None:
    command = template.format(reference=reference)

    result = _scheduler_preflight_bash_template(
        tmp_path,
        monkeypatch,
        command,
    )

    assert result, context


@pytest.mark.parametrize(
    ("delimiter", "delimiter_kind"),
    _QUOTED_HEREDOC_DELIMITERS,
    ids=[kind for _delimiter, kind in _QUOTED_HEREDOC_DELIMITERS],
)
def test_v3_scheduler_preserves_quoted_heredoc_body_continuations(
    tmp_path,
    monkeypatch,
    delimiter,
    delimiter_kind,
) -> None:
    command = (
        f"cat <<{delimiter} >/dev/null\nline\\\nEOF\nprintf '%s' \"$USER_MESSAGE\""
    )

    result = _scheduler_preflight_bash_template(
        tmp_path,
        monkeypatch,
        command,
    )

    assert result, delimiter_kind


@pytest.mark.parametrize(
    ("template", "context"),
    _JOINED_HEREDOC_CONTEXTS,
    ids=[context for _template, context in _JOINED_HEREDOC_CONTEXTS],
)
def test_v3_scheduler_parses_logically_joined_heredocs(
    tmp_path,
    monkeypatch,
    template,
    context,
) -> None:
    command = template.format(reference="$USER_MESSAGE")

    assert _scheduler_preflight_bash_template(
        tmp_path,
        monkeypatch,
        command,
    ), context


@pytest.mark.parametrize(
    ("delimiter", "delimiter_kind"),
    _COMMENT_FOLLOWED_HEREDOC_DELIMITERS,
    ids=[kind for _delimiter, kind in _COMMENT_FOLLOWED_HEREDOC_DELIMITERS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_scheduler_rejects_real_heredocs_after_physical_comments(
    tmp_path,
    monkeypatch,
    delimiter,
    delimiter_kind,
    reference,
) -> None:
    command = f"# physical comment \\\n<<{delimiter}\n{reference}\nEOF\n"

    with pytest.raises(BashRenderingError) as exc:
        _scheduler_preflight_bash_template(
            tmp_path,
            monkeypatch,
            command,
            depends_on=("producer",) if reference == "$producer.output" else (),
        )

    assert exc.value.code == "bash_reference_context_unsupported", delimiter_kind


@pytest.mark.parametrize(
    ("template", "context"),
    (*_PROCESS_SUBSTITUTION_CONTEXTS, *_PRIOR_CONTINUATION_COMMENT_HEREDOC_CONTEXTS),
    ids=[
        context
        for _template, context in (
            *_PROCESS_SUBSTITUTION_CONTEXTS,
            *_PRIOR_CONTINUATION_COMMENT_HEREDOC_CONTEXTS,
        )
    ],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_scheduler_rejects_nested_or_physical_heredoc_contexts_before_resolution(
    tmp_path,
    monkeypatch,
    template,
    context,
    reference,
) -> None:
    command = template.format(reference=reference)

    with pytest.raises(BashRenderingError) as exc:
        _scheduler_preflight_bash_template(
            tmp_path,
            monkeypatch,
            command,
            depends_on=("producer",) if reference == "$producer.output" else (),
        )

    assert exc.value.code == "bash_reference_context_unsupported", context


@pytest.mark.parametrize(
    ("template", "context"),
    _UNSAFE_WORD_EXPANSION_CONTEXTS,
    ids=[context for _template, context in _UNSAFE_WORD_EXPANSION_CONTEXTS],
)
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_scheduler_rejects_extglob_and_brace_expansion_before_resolution(
    tmp_path,
    monkeypatch,
    template,
    context,
    reference,
) -> None:
    command = template.format(reference=reference)

    with pytest.raises(BashRenderingError) as exc:
        _scheduler_preflight_bash_template(
            tmp_path,
            monkeypatch,
            command,
            depends_on=("producer",) if reference == "$producer.output" else (),
        )

    assert exc.value.code == "bash_reference_context_unsupported", context


@pytest.mark.parametrize(
    ("template", "context"),
    _PHASE_REENTRY_SAFE_CONTEXTS,
    ids=[context for _template, context in _PHASE_REENTRY_SAFE_CONTEXTS],
)
def test_v3_scheduler_accepts_safe_reference_after_phase_reentry_constructs(
    tmp_path,
    monkeypatch,
    template,
    context,
) -> None:
    command = template.format(reference="$USER_MESSAGE")

    assert _scheduler_preflight_bash_template(
        tmp_path,
        monkeypatch,
        command,
    ), context


@pytest.mark.parametrize(
    ("template", "context"),
    _HERE_STRING_CONTEXTS,
    ids=[context for _template, context in _HERE_STRING_CONTEXTS],
)
def test_v3_scheduler_maximal_munch_accepts_here_string_operand(
    tmp_path,
    monkeypatch,
    template,
    context,
) -> None:
    command = template.format(reference="$USER_MESSAGE")

    assert _scheduler_preflight_bash_template(
        tmp_path,
        monkeypatch,
        command,
    ), context


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
@pytest.mark.parametrize("reference", ("$USER_MESSAGE", "$producer.output"))
def test_v3_bash_keeps_arithmetic_punctuation_as_quoted_simple_token_data(
    tmp_path,
    reference,
) -> None:
    value = "simple token " + "q" * 32_769
    output_reference = reference == "$producer.output"
    dependencies = ("producer",) if output_reference else ()
    renderer = substitution_renderer(
        VariableContext(user_message=value, normalizer_version=3),
        direct_dependencies=dependencies,
        output_resolver=(
            (lambda _node_id, _path: ResolvedOutputReference(value, value))
            if output_reference
            else None
        ),
    )
    node = WorkflowNode(
        id="shell",
        node_type="bash",
        value=f"printf '%s' \"(( {reference} ))\"",
        depends_on=dependencies,
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )

    result = BashExecutor().execute(
        NodeExecutionContext(
            run_id="quoted-arithmetic-data",
            run_directory=tmp_path,
            node=node,
            attempt_id="attempt-1",
            variable_context=renderer,
            language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=3,
        )
    )

    assert result.status == "succeeded"
    assert (tmp_path / "nodes" / "shell" / "attempt-1" / "stdout.txt").read_text(
        encoding="utf-8"
    ) == f"(( {value} ))"


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("printf '%s' \\$USER_MESSAGE", "$USER_MESSAGE"),
        ("# $USER_MESSAGE\nprintf safe", "safe"),
        ("\\\n# $USER_MESSAGE\nprintf safe", "safe"),
        ("printf '%s' \"$( # $USER_MESSAGE\nprintf safe)\"", "safe"),
        ("printf '%s' word#$USER_MESSAGE", "word#data"),
    ],
)
def test_v3_bash_ignores_escaped_and_comment_references_without_spilling(
    tmp_path,
    command,
    expected,
) -> None:
    value = "data" if expected.endswith("data") else "z" * 32_769

    result, output = _run_v3_bash(tmp_path, command=command, value=value)

    assert result.status == "succeeded"
    assert output == expected.encode("utf-8")
    assert not (tmp_path / "nodes" / "shell" / "attempt-1" / "variables-v3").exists()


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
def test_v3_bash_admits_data_tokens_before_redirection(tmp_path) -> None:
    output_path = tmp_path / "redirected"
    payload = "$(touch injected) * 'quoted'"

    result, output = _run_v3_bash(
        tmp_path,
        command=f"printf '%s' $USER_MESSAGE > {output_path}",
        value=payload,
    )

    assert result.status == "succeeded"
    assert output == b""
    assert output_path.read_text(encoding="utf-8") == payload
    assert not (tmp_path / "injected").exists()


@pytest.mark.parametrize(
    ("value", "expected_code"),
    [
        ("before\0after", "bash_substitution_nul"),
        ("z" * 500_001, "bash_substitution_limit"),
    ],
    ids=("nul", "per-value-limit"),
)
def test_v3_bash_rejects_invalid_value_bounds_before_launch(
    tmp_path,
    value,
    expected_code,
) -> None:
    launched: list[str] = []

    result, output = _run_v3_bash(
        tmp_path,
        command="printf '%s' $USER_MESSAGE",
        value=value,
        spawn_intent=lambda nonce: launched.append(nonce) or True,
    )

    assert result.status == "failed"
    assert result.error_code == expected_code
    assert launched == []
    assert output is None


def test_v3_bash_rejects_total_spill_bytes_before_materialization(tmp_path) -> None:
    values = tuple(chr(65 + index) * 400_000 for index in range(6))
    template = " ".join(f"token-{index}" for index in range(len(values)))
    substitutions = []
    cursor = 0
    for index, value in enumerate(values):
        token = f"token-{index}"
        start = template.index(token, cursor)
        substitutions.append((start, start + len(token), value))
        cursor = start + len(token)

    with pytest.raises(BashRenderingError) as exc:
        render_v3_bash(
            template,
            substitutions,
            spill_directory=tmp_path / "variables-v3",
        )

    assert exc.value.code == "bash_substitution_limit"
    assert not (tmp_path / "variables-v3").exists()


def test_v3_bash_accepts_exact_per_value_and_total_spill_byte_limits(
    tmp_path,
) -> None:
    values = tuple(chr(65 + index) * 500_000 for index in range(4))
    template = " ".join(f"token-{index}" for index in range(len(values)))
    substitutions = tuple(
        (
            template.index(f"token-{index}"),
            template.index(f"token-{index}") + len(f"token-{index}"),
            value,
        )
        for index, value in enumerate(values)
    )

    rendered = render_v3_bash(
        template,
        substitutions,
        spill_directory=tmp_path / "variables-v3",
    )

    try:
        assert rendered.spill_count == 4
        assert rendered.spill_total_bytes == 2_000_000
        assert len(rendered.inherited_descriptors) == 4
    finally:
        rendered.close()


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
def test_v3_bash_deduplicates_spills_and_records_bounded_descriptor_evidence(
    tmp_path,
    monkeypatch,
) -> None:
    value = "distinct spill " + "z" * 32_769 + "x\n\n"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    template = "printf '%s|%s' \"$USER_MESSAGE\" '$USER_MESSAGE'"
    captured_argv: list[list[str]] = []
    original_spawn = ManagedProcessTree.spawn

    def capture_spawn(argv, **kwargs):
        captured_argv.append(list(argv))
        return original_spawn(argv, **kwargs)

    monkeypatch.setattr(ManagedProcessTree, "spawn", staticmethod(capture_spawn))

    result, output = _run_v3_bash(
        tmp_path,
        command=template,
        value=value,
    )

    assert result.status == "succeeded"
    assert output == f"{value}|{value}".encode("utf-8")
    evidence = result.metadata["bash"]
    assert evidence["spill_count"] == 1
    assert evidence["spill_total_bytes"] == len(value.encode("utf-8"))
    assert evidence["template_sha256"] == hashlib.sha256(template.encode()).hexdigest()
    assert evidence["template_size_bytes"] == len(template.encode())
    assert (
        evidence["rendered_sha256"]
        == hashlib.sha256(captured_argv[0][-1].encode()).hexdigest()
    )
    assert evidence["rendered_size_bytes"] == len(captured_argv[0][-1].encode())
    assert evidence["spill_content_sha256"] == [digest]
    assert evidence["descriptor_manifest"] == [
        {
            "descriptor": evidence["descriptor_manifest"][0]["descriptor"],
            "sha256": digest,
        }
    ]
    descriptor = evidence["descriptor_manifest"][0]["descriptor"]
    variable = f"__HERMES_WF_SPILL_{digest}"
    assert captured_argv[0][-1] == (
        f"{variable}=$(command cat <&{descriptor}; __hermes_rc=$?; printf x; "
        'exit "$__hermes_rc") || exit $?\n'
        f"{variable}=${{{variable}%x}}\n"
        f"printf '%s|%s' \"${{{variable}}}\" ''\"${{{variable}}}\"''"
    )
    with pytest.raises(OSError):
        os.fstat(descriptor)
    serialized = json.dumps(evidence, sort_keys=True)
    assert value not in serialized
    assert "variables-v3" not in serialized
    assert str(tmp_path) not in serialized


@pytest.mark.skipif(os.name == "nt", reason="real POSIX descriptor contract")
def test_v3_bash_ignores_recreated_spill_path_after_snapshot_detachment(
    tmp_path,
    monkeypatch,
) -> None:
    value = "verified original " + "v" * 32_769 + "x\n\n"
    replacement = b"attacker replacement"
    original_spawn = ManagedProcessTree.spawn
    path_presence: list[bool] = []

    def replace_path_before_spawn(argv, **kwargs):
        spill = (
            tmp_path / "nodes" / "shell" / "attempt-1" / "variables-v3" / "spill-0000"
        )
        path_presence.append(spill.exists())
        spill.parent.mkdir()
        spill.write_bytes(replacement)
        return original_spawn(argv, **kwargs)

    monkeypatch.setattr(
        ManagedProcessTree,
        "spawn",
        staticmethod(replace_path_before_spawn),
    )

    result, output = _run_v3_bash(
        tmp_path,
        command="printf '%s' $USER_MESSAGE",
        value=value,
    )

    assert result.status == "succeeded"
    assert output == value.encode()
    assert path_presence == [False]
    assert replacement != output


def test_v3_bash_fails_closed_when_spill_root_is_swapped_for_symlink(
    tmp_path,
    monkeypatch,
) -> None:
    target = tmp_path / "escape-target"
    target.mkdir()
    original_mkdir = bash_rendering.os.mkdir

    def symlink_instead(path, mode=0o777, *, dir_fd=None):
        os.symlink(target, path, dir_fd=dir_fd)

    monkeypatch.setattr(bash_rendering.os, "mkdir", symlink_instead)
    try:
        with pytest.raises(BashRenderingError) as exc:
            render_v3_bash(
                "$USER_MESSAGE",
                ((0, len("$USER_MESSAGE"), "s" * 32_769),),
                spill_directory=tmp_path / "variables-v3",
            )
    finally:
        monkeypatch.setattr(bash_rendering.os, "mkdir", original_mkdir)

    assert exc.value.code == "bash_spill_integrity"
    assert list(target.iterdir()) == []


def test_v3_bash_rejects_symlink_in_intermediate_spill_descriptor_chain(
    tmp_path,
) -> None:
    escape = tmp_path / "escape"
    (escape / "attempt").mkdir(parents=True)
    (tmp_path / "alias").symlink_to(escape, target_is_directory=True)

    with pytest.raises(BashRenderingError) as exc:
        render_v3_bash(
            "$USER_MESSAGE",
            ((0, len("$USER_MESSAGE"), "e" * 32_769),),
            spill_directory=tmp_path / "alias" / "attempt" / "variables-v3",
        )

    assert exc.value.code == "bash_spill_integrity"
    assert not (escape / "attempt" / "variables-v3").exists()


@pytest.mark.skipif(os.name == "nt", reason="real POSIX descriptor contract")
def test_v3_bash_maps_closed_spill_descriptor_to_integrity_failure(
    tmp_path,
    monkeypatch,
) -> None:
    original_spawn = ManagedProcessTree.spawn

    def close_before_spawn(argv, *, inherited_descriptors=(), **kwargs):
        for descriptor in inherited_descriptors:
            os.close(descriptor)
        return original_spawn(
            argv,
            inherited_descriptors=inherited_descriptors,
            **kwargs,
        )

    monkeypatch.setattr(
        ManagedProcessTree,
        "spawn",
        staticmethod(close_before_spawn),
    )

    result, output = _run_v3_bash(
        tmp_path,
        command="printf '%s' $USER_MESSAGE",
        value="c" * 32_769,
    )

    assert result.status == "failed"
    assert result.error_code == "bash_spill_integrity"
    assert output == b""


@pytest.mark.skipif(os.name == "nt", reason="real POSIX descriptor contract")
def test_v3_bash_never_pairs_original_evidence_with_reused_descriptor_bytes(
    tmp_path,
    monkeypatch,
) -> None:
    value = "verified original " + "v" * 32_769
    original = value.encode("utf-8")
    replacement = b"readable replacement bytes"
    replacement_path = tmp_path / "replacement"
    replacement_path.write_bytes(replacement)
    original_spawn = ManagedProcessTree.spawn

    def reuse_descriptor_before_managed_pin(
        argv,
        *,
        inherited_descriptors=(),
        **kwargs,
    ):
        assert len(inherited_descriptors) == 1
        exposed = inherited_descriptors[0]
        os.close(exposed)
        replacement_descriptor = os.open(replacement_path, os.O_RDONLY)
        if replacement_descriptor != exposed:
            os.dup2(replacement_descriptor, exposed)
            os.close(replacement_descriptor)
        return original_spawn(
            argv,
            inherited_descriptors=inherited_descriptors,
            **kwargs,
        )

    monkeypatch.setattr(
        ManagedProcessTree,
        "spawn",
        staticmethod(reuse_descriptor_before_managed_pin),
    )

    result, output = _run_v3_bash(
        tmp_path,
        command="printf '%s' $USER_MESSAGE",
        value=value,
    )

    evidence = result.metadata["bash"]
    assert evidence["spill_content_sha256"] == [hashlib.sha256(original).hexdigest()]
    assert output != replacement
    assert (result.status == "succeeded" and output == original) or (
        result.status == "failed" and result.error_code == "bash_spill_integrity"
    )


@pytest.mark.skipif(os.name == "nt", reason="real POSIX descriptor contract")
def test_v3_bash_closes_spill_descriptors_when_spawn_intent_is_rejected(
    tmp_path,
    monkeypatch,
) -> None:
    observed: list[int] = []
    original_materialize = bash_rendering._materialize_spills

    def record_descriptors(*args, **kwargs):
        transport = original_materialize(*args, **kwargs)
        observed.extend(transport.read_descriptors)
        return transport

    monkeypatch.setattr(
        bash_rendering,
        "_materialize_spills",
        record_descriptors,
    )

    with pytest.raises(RuntimeError, match="spawn intent was rejected"):
        _run_v3_bash(
            tmp_path,
            command="printf '%s' $USER_MESSAGE",
            value="i" * 32_769,
            spawn_intent=lambda _nonce: False,
        )

    assert len(observed) == 1
    with pytest.raises(OSError):
        os.fstat(observed[0])


@pytest.mark.skipif(os.name == "nt", reason="real POSIX descriptor contract")
def test_bash_spill_materializer_enforces_64_file_bound_and_modes(
    tmp_path,
    monkeypatch,
) -> None:
    spills = tuple(
        (bytes([index]), hashlib.sha256(bytes([index])).hexdigest())
        for index in range(64)
    )
    observed_files: list[os.stat_result] = []
    original_unlink = bash_rendering.os.unlink

    def record_verified_file(path, *, dir_fd=None):
        observed_files.append(os.stat(path, dir_fd=dir_fd, follow_symlinks=False))
        original_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(bash_rendering.os, "unlink", record_verified_file)

    transport = bash_rendering._materialize_spills(
        tmp_path / "sixty-four",
        spills,
    )

    try:
        assert len(transport.read_descriptors) == 64
        assert len(observed_files) == 64
        assert not (tmp_path / "sixty-four").exists()
        for descriptor in transport.read_descriptors:
            descriptor_stat = os.fstat(descriptor)
            assert stat.S_ISFIFO(descriptor_stat.st_mode)
            with pytest.raises(OSError):
                os.write(descriptor, b"not writable")
    finally:
        transport.close()

    assert all(stat.S_ISREG(item.st_mode) for item in observed_files)
    assert all(item.st_nlink == 1 for item in observed_files)
    assert all(stat.S_IMODE(item.st_mode) == 0o600 for item in observed_files)

    with pytest.raises(BashRenderingError) as exc:
        bash_rendering._materialize_spills(
            tmp_path / "sixty-five",
            spills + ((b"extra", hashlib.sha256(b"extra").hexdigest()),),
        )
    assert exc.value.code == "bash_substitution_limit"
    assert not (tmp_path / "sixty-five").exists()


def test_v3_large_bash_substitution_fails_closed_on_native_windows(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(bash_rendering, "_NATIVE_WINDOWS", True)

    with pytest.raises(BashRenderingError) as exc:
        render_v3_bash(
            "$USER_MESSAGE",
            ((0, len("$USER_MESSAGE"), "w" * 32_769),),
            spill_directory=tmp_path / "variables-v3",
        )

    assert exc.value.code == "bash_spill_integrity"
    assert not (tmp_path / "variables-v3").exists()


def test_v3_large_bash_substitution_requires_descriptor_safe_host_support(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(bash_rendering, "_DESCRIPTOR_SPILLS_SUPPORTED", False)

    with pytest.raises(BashRenderingError) as exc:
        render_v3_bash(
            "$USER_MESSAGE",
            ((0, len("$USER_MESSAGE"), "h" * 32_769),),
            spill_directory=tmp_path / "variables-v3",
        )

    assert exc.value.code == "bash_spill_integrity"
    assert not (tmp_path / "variables-v3").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX host simulates Windows argv gate")
def test_v3_native_windows_inline_executor_keeps_exact_argv_and_containment(
    tmp_path,
    monkeypatch,
) -> None:
    command = "sleep 0.05; printf '%s' \"$USER_MESSAGE\""
    value = "inline value"
    captured: dict[str, object] = {}
    original_name = os.name
    original_spawn = ManagedProcessTree.spawn

    def capture_spawn(argv, *, inherited_descriptors=(), **kwargs):
        captured["argv"] = list(argv)
        captured["inherited_descriptors"] = tuple(inherited_descriptors)
        monkeypatch.setattr(os, "name", original_name)
        tree = original_spawn(
            argv,
            inherited_descriptors=inherited_descriptors,
            **kwargs,
        )
        captured["tree"] = tree
        return tree

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr("tools.environments.local._find_bash", lambda: "/bin/sh")
    monkeypatch.setattr(
        ManagedProcessTree,
        "spawn",
        staticmethod(capture_spawn),
    )

    result, output = _run_v3_bash(
        tmp_path,
        command=command,
        value=value,
    )

    assert result.status == "succeeded"
    assert output == b"inline value"
    assert captured["argv"] == [
        "/bin/sh",
        "-c",
        "sleep 0.05; printf '%s' \"inline value\"",
    ]
    assert captured["inherited_descriptors"] == ()
    tree = captured["tree"]
    assert isinstance(tree, ManagedProcessTree)
    assert tree.identity.group_id == tree.identity.pid
    assert tree.reaped is True


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh descriptor failure contract")
def test_v3_shell_read_failure_after_launch_is_not_masked_by_sentinel(
    tmp_path,
    monkeypatch,
) -> None:
    template = "printf '%s' $USER_MESSAGE"
    value = "read failure " + "r" * 32_769
    rendered = render_v3_bash(
        template,
        ((template.index("$USER_MESSAGE"), len(template), value),),
        spill_directory=tmp_path / "prepared-spill",
    )
    descriptor = rendered.inherited_descriptors[0]
    captured_trees: list[ManagedProcessTree] = []
    original_spawn = ManagedProcessTree.spawn
    original_pin = managed_process_module._pin_read_only_descriptors

    class ReadFailureRenderer:
        @staticmethod
        def render_bash(command, *, spill_directory, secure_v3):
            assert command == template
            assert secure_v3 is True
            return rendered

    def capture_spawn(argv, **kwargs):
        tree = original_spawn(argv, **kwargs)
        captured_trees.append(tree)
        return tree

    def corrupt_pin_after_identity_check(descriptors, expected_identities=()):
        pins = original_pin(descriptors, expected_identities)
        directory_descriptor = os.open(tmp_path, os.O_RDONLY)
        try:
            os.dup2(directory_descriptor, pins[0][0])
        finally:
            os.close(directory_descriptor)
        return pins

    monkeypatch.setattr(
        managed_process_module,
        "_pin_read_only_descriptors",
        corrupt_pin_after_identity_check,
    )
    monkeypatch.setattr(
        ManagedProcessTree,
        "spawn",
        staticmethod(capture_spawn),
    )
    node = WorkflowNode(
        id="shell",
        node_type="bash",
        value=template,
        depends_on=(),
        source_index=0,
        source_line=1,
        options=freeze_value({}),
    )

    result = BashExecutor().execute(
        NodeExecutionContext(
            run_id="read-failure",
            run_directory=tmp_path,
            node=node,
            attempt_id="attempt-1",
            variable_context=ReadFailureRenderer(),
            language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version=3,
        )
    )

    assert result.status == "failed"
    assert result.error_code == "process_exit"
    assert (
        tmp_path / "nodes" / "shell" / "attempt-1" / "stdout.txt"
    ).read_bytes() == b""
    assert (
        tmp_path / "nodes" / "shell" / "attempt-1" / "stderr.txt"
    ).stat().st_size > 0
    with pytest.raises(OSError):
        os.fstat(descriptor)
    assert len(captured_trees) == 1
    assert captured_trees[0].reaped is True


@pytest.mark.skipif(os.name == "nt", reason="real /bin/sh contract")
def test_legacy_bash_keeps_pathname_spill_behavior(tmp_path) -> None:
    value = "legacy-value-" + "l" * 9_000

    result, output = _run_v3_bash(
        tmp_path,
        command="printf '%s' $USER_MESSAGE",
        value=value,
        profile=WorkflowLanguageProfile.HERMES_LEGACY,
    )

    assert result.status == "succeeded"
    spill_path = tmp_path / output.decode()
    assert spill_path.name.startswith("variable-")
    assert spill_path.read_text(encoding="utf-8") == value
