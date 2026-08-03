from __future__ import annotations

import hashlib
import json
import os
import stat

import pytest

import plugins.workflow.bash_rendering as bash_rendering
from plugins.workflow.bash_rendering import BashRenderingError, render_v3_bash
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.executors.bash import BashExecutor
from plugins.workflow.models import WorkflowLanguageProfile, WorkflowNode, freeze_value
from plugins.workflow.resources import VariableContext, substitution_renderer
from tools.managed_process import ManagedProcessTree


_SHELL_CONTEXTS = {
    "unquoted": "printf '%s' $USER_MESSAGE",
    "double-quoted": "printf '%s' \"$USER_MESSAGE\"",
    "single-quoted": "printf '%s' '$USER_MESSAGE'",
}


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
            spawn_intent=spawn_intent,
        )
    )
    output_path = tmp_path / "nodes" / "shell" / "attempt-1" / "stdout.txt"
    output = output_path.read_bytes() if output_path.exists() else None
    return result, output


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
    assert spill_root.exists() is (byte_count > 32_768)


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
        for descriptor in rendered.inherited_descriptors:
            os.close(descriptor)


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
def test_v3_bash_uses_verified_descriptor_after_spill_path_replacement(
    tmp_path,
    monkeypatch,
) -> None:
    value = "verified original " + "v" * 32_769 + "x\n\n"
    replacement = b"attacker replacement"
    original_spawn = ManagedProcessTree.spawn

    def replace_path_before_spawn(argv, **kwargs):
        spill = (
            tmp_path / "nodes" / "shell" / "attempt-1" / "variables-v3" / "spill-0000"
        )
        spill.unlink()
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
def test_v3_bash_closes_spill_descriptors_when_spawn_intent_is_rejected(
    tmp_path,
    monkeypatch,
) -> None:
    observed: list[int] = []
    original_verify = bash_rendering._verified_spill_descriptor

    def record_descriptor(*args, **kwargs):
        descriptor = original_verify(*args, **kwargs)
        observed.append(descriptor)
        return descriptor

    monkeypatch.setattr(
        bash_rendering,
        "_verified_spill_descriptor",
        record_descriptor,
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
def test_bash_spill_materializer_enforces_64_file_bound_and_modes(tmp_path) -> None:
    spills = tuple(
        (bytes([index]), hashlib.sha256(bytes([index])).hexdigest())
        for index in range(64)
    )

    descriptors = bash_rendering._materialize_spills(
        tmp_path / "sixty-four",
        spills,
    )

    try:
        assert len(descriptors) == 64
        for index, descriptor in enumerate(descriptors):
            descriptor_stat = os.fstat(descriptor)
            path_stat = (tmp_path / "sixty-four" / f"spill-{index:04d}").stat()
            assert stat.S_ISREG(descriptor_stat.st_mode)
            assert descriptor_stat.st_nlink == 1
            assert stat.S_IMODE(path_stat.st_mode) == 0o600
            with pytest.raises(OSError):
                os.write(descriptor, b"not writable")
    finally:
        for descriptor in descriptors:
            os.close(descriptor)

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
