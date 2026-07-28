"""Behavior contracts for the upstream-customization ledger checker."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

import pytest
import yaml

import scripts.check_upstream_customizations as customization_checker
from scripts.check_upstream_customizations import (
    classify_upstream_overlap,
    load_and_validate_manifest,
    validate_diff_coverage,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "core.py").write_text("class Owned:\n    pass\n")
    (repo / "test_core.py").write_text("def test_owned():\n    pass\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    return repo


def _manifest(repo: Path, baseline: str) -> Path:
    path = repo / "ledger.yaml"
    path.write_text(yaml.safe_dump({
        "schema_version": 1,
        "feature": "test-feature",
        "upstream_changes": [{
            "id": "owned",
            "change_class": "agent-core-generic",
            "owner": "test-feature",
            "files": ["core.py"],
            "owned_symbols": ["Owned"],
            "tests": ["test_core.py"],
            "expected_commit_subject": "feat: owned",
            "upstream_candidate": True,
            "merge_guidance": "Reconcile behavior.",
            "removal_condition": "Remove after equivalent upstream support.",
            "last_verified_upstream": baseline,
        }],
    }, sort_keys=False))
    return path


def test_manifest_rejects_non_hex_and_paths_outside_repository(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest(repo, "not-a-sha")
    with pytest.raises(ValueError, match="40-hex"):
        load_and_validate_manifest(manifest, repo)

    data = yaml.safe_load(manifest.read_text())
    data["upstream_changes"][0]["last_verified_upstream"] = "a" * 40
    data["upstream_changes"][0]["files"] = ["../escape.py"]
    manifest.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="normalized repository-relative POSIX"):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_manifest_bounds_evidence_identity_and_exact_repository_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    data = yaml.safe_load(manifest.read_text())

    data["upstream_changes"][0]["id"] = "i" * 513
    manifest.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="id must be at most 512"):
        load_and_validate_manifest(manifest, repo, check_git=False)

    data["upstream_changes"][0]["id"] = "owned"
    monkeypatch.setattr(
        customization_checker,
        "_contained",
        lambda _repo, _raw: repo / "core.py",
    )
    data["upstream_changes"][0]["tests"] = ["t" * 4096]
    manifest.write_text(yaml.safe_dump(data))
    load_and_validate_manifest(manifest, repo, check_git=False)

    data["upstream_changes"][0]["tests"] = ["t" * 4097]
    manifest.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="path must be at most 4096"):
        load_and_validate_manifest(manifest, repo, check_git=False)

    data["upstream_changes"][0]["tests"] = ["./test_core.py"]
    manifest.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="normalized repository-relative POSIX"):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_diff_coverage_detects_add_delete_and_rename(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    data = load_and_validate_manifest(manifest, repo)

    (repo / "unledgered.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feat: unledgered")
    with pytest.raises(ValueError, match="unledgered.py"):
        validate_diff_coverage(data, repo, f"{baseline}..HEAD")

    _git(repo, "mv", "core.py", "renamed.py")
    _git(repo, "commit", "-m", "rename", "-a")
    with pytest.raises(ValueError, match="renamed.py"):
        validate_diff_coverage(data, repo, "HEAD~1..HEAD")


def test_diff_coverage_requires_ledger_for_existing_plugin_files(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    plugin = repo / "plugins/kanban/dashboard/plugin_api.py"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("value = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add upstream plugin")
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    data = load_and_validate_manifest(manifest, repo)

    plugin.write_text("value = 2\n")
    _git(repo, "commit", "-am", "change existing upstream plugin")

    with pytest.raises(ValueError, match="plugins/kanban/dashboard/plugin_api.py"):
        validate_diff_coverage(data, repo, f"{baseline}..HEAD")


def test_diff_coverage_ignores_new_additive_plugin_directory(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    data = load_and_validate_manifest(manifest, repo)
    plugin = repo / "plugins/new-feature/plugin.py"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("value = 1\n")
    _git(repo, "add", str(plugin.relative_to(repo)))
    _git(repo, "commit", "-m", "add feature plugin")

    validate_diff_coverage(data, repo, f"{baseline}..HEAD")


def test_overlap_classification_distinguishes_file_symbol_and_equivalent(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    entry = load_and_validate_manifest(manifest, repo)["upstream_changes"][0]

    (repo / "core.py").write_text("class Owned:\n    value = 1\n")
    _git(repo, "commit", "-am", "upstream owned symbol")
    assert classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")["classification"] == "owned_symbol"

    second = _git(repo, "rev-parse", "HEAD")
    (repo / "core.py").write_text("class Owned:\n    value = 1\n# unrelated\n")
    _git(repo, "commit", "-am", "same file only")
    assert classify_upstream_overlap(entry, repo, f"{second}..HEAD")["classification"] == "same_file"

    third = _git(repo, "rev-parse", "HEAD")
    (repo / "replacement.py").write_text("class Owned:\n    pass\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "equivalent public contract")
    assert classify_upstream_overlap(entry, repo, f"{third}..HEAD")["classification"] == "possible_upstream_equivalent"


def test_any_owned_file_overlap_requires_explicit_decision(tmp_path: Path) -> None:
    """Removing the policy branch would let same-file security churn continue."""
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    raw = yaml.safe_load(manifest.read_text())
    raw["upstream_changes"][0]["overlap_policy"] = "any_owned_file"
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))
    entry = load_and_validate_manifest(manifest, repo)["upstream_changes"][0]

    (repo / "core.py").write_text("class Owned:\n    pass\n# upstream security edit\n")
    _git(repo, "commit", "-am", "upstream same-file security edit")

    overlap = classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")
    assert overlap["classification"] == "same_file"
    assert overlap["decision_required"] is True


def test_strict_owned_symbol_must_exist_in_declared_files(tmp_path: Path) -> None:
    """Renaming away a strict identifier must invalidate the ledger immediately."""
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    raw = yaml.safe_load(manifest.read_text())
    raw["upstream_changes"][0]["overlap_policy"] = "owned_symbol"
    raw["upstream_changes"][0]["owned_symbols"] = ["RenamedAway"]
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))

    with pytest.raises(ValueError, match="RenamedAway.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    ("source", "symbol"),
    [
        ("class Owned:\n    pass\n# CommentOnly\n", "CommentOnly"),
        (
            "class Owned:\n    pass\n\nclass OtherOwner:\n"
            "    def terminal(self):\n        pass\n",
            "MissingOwner.terminal",
        ),
    ],
)
def test_strict_owned_symbol_ignores_comments_and_qualified_collisions(
    tmp_path: Path,
    source: str,
    symbol: str,
) -> None:
    repo = _repo(tmp_path)
    (repo / "core.py").write_text(source)
    _git(repo, "commit", "-am", "replace symbol source")
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    raw = yaml.safe_load(manifest.read_text())
    raw["upstream_changes"][0]["overlap_policy"] = "owned_symbol"
    raw["upstream_changes"][0]["owned_symbols"] = [symbol]
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))

    with pytest.raises(ValueError, match=rf"{re.escape(symbol)}.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_strict_owned_symbol_uses_committed_head_not_dirty_bytes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    raw = yaml.safe_load(manifest.read_text())
    raw["upstream_changes"][0]["overlap_policy"] = "owned_symbol"
    raw["upstream_changes"][0]["owned_symbols"] = ["DirtyOnly"]
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))
    (repo / "core.py").write_text("class Owned:\n    pass\n\nclass DirtyOnly:\n    pass\n")

    with pytest.raises(ValueError, match="DirtyOnly.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_manifest_rejects_malformed_policy_and_invariant_shapes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    raw = yaml.safe_load(manifest.read_text())
    entry = raw["upstream_changes"][0]

    entry["overlap_policy"] = ["owned_symbol"]
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))
    with pytest.raises(ValueError, match="overlap_policy must be a string"):
        load_and_validate_manifest(manifest, repo, check_git=False)

    entry["overlap_policy"] = "owned_symbol"
    entry["owned_invariants"] = {"not": "a list"}
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))
    with pytest.raises(ValueError, match="owned_invariants must be a list"):
        load_and_validate_manifest(manifest, repo, check_git=False)

    entry["owned_invariants"] = ["bounded"] * 129
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))
    with pytest.raises(ValueError, match="at most 128"):
        load_and_validate_manifest(manifest, repo, check_git=False)

    entry["owned_invariants"] = ["x" * 513]
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))
    with pytest.raises(ValueError, match="bounded non-empty prose"):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_overlap_uses_non_head_right_blob_not_checkout_bytes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    entry = load_and_validate_manifest(_manifest(repo, baseline), repo)[
        "upstream_changes"
    ][0]
    (repo / "core.py").write_text(
        "class Owned:\n    pass\n\n    def added_on_right(self):\n        return True\n"
    )
    _git(repo, "commit", "-am", "right changes owned span")
    right = _git(repo, "rev-parse", "HEAD")
    (repo / "core.py").write_text("class Owned:\n    pass\n")
    _git(repo, "commit", "-am", "later checkout no longer has right bytes")

    overlap = classify_upstream_overlap(entry, repo, f"{baseline}..{right}")

    assert overlap["classification"] == "owned_symbol"


def test_overlap_triple_dot_uses_merge_base_and_non_head_right(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    entry = load_and_validate_manifest(_manifest(repo, baseline), repo)[
        "upstream_changes"
    ][0]
    _git(repo, "checkout", "-b", "left")
    (repo / "core.py").write_text("class Owned:\n    left_only = True\n")
    _git(repo, "commit", "-am", "left owned change")
    left = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "right", baseline)
    (repo / "core.py").write_text("class Owned:\n    pass\n# right same-file edit\n")
    _git(repo, "commit", "-am", "right same-file change")
    right = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "left")

    overlap = classify_upstream_overlap(entry, repo, f"{left}...{right}")

    assert overlap["classification"] == "same_file"


def test_stacked_decorator_only_change_hits_owned_function_span(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "core.py").write_text(
        "def outer(value):\n    return value\n\n"
        "def old(value):\n    return value\n\n"
        "def new(value):\n    return value\n\n"
        "@outer\n@old\ndef owned():\n    return True\n"
    )
    _git(repo, "commit", "-am", "install stacked decorated function")
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    raw = yaml.safe_load(manifest.read_text())
    raw["upstream_changes"][0]["overlap_policy"] = "owned_symbol"
    raw["upstream_changes"][0]["owned_symbols"] = ["owned"]
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))
    entry = load_and_validate_manifest(manifest, repo)["upstream_changes"][0]

    (repo / "core.py").write_text((repo / "core.py").read_text().replace("@old", "@new"))
    _git(repo, "commit", "-am", "change only inner decorator")

    overlap = classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")

    assert overlap["classification"] == "owned_symbol"


def test_nested_decorator_change_uses_non_head_right_definition_blob(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    original = (
        "def outer(value):\n    return value\n\n"
        "def old(value):\n    return value\n\n"
        "def new(value):\n    return value\n\n"
        "class Owner:\n"
        "    @outer\n"
        "    @old\n"
        "    def owned(self):\n"
        "        return True\n"
    )
    (repo / "core.py").write_text(original)
    _git(repo, "commit", "-am", "install nested decorated method")
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    raw = yaml.safe_load(manifest.read_text())
    raw["upstream_changes"][0]["overlap_policy"] = "owned_symbol"
    raw["upstream_changes"][0]["owned_symbols"] = ["Owner.owned"]
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))
    entry = load_and_validate_manifest(manifest, repo)["upstream_changes"][0]

    (repo / "core.py").write_text(original.replace("@old", "@new"))
    _git(repo, "commit", "-am", "change only nested decorator")
    right = _git(repo, "rev-parse", "HEAD")
    (repo / "core.py").write_text(original)
    _git(repo, "commit", "-am", "move checkout beyond reviewed right blob")

    overlap = classify_upstream_overlap(entry, repo, f"{baseline}..{right}")

    assert overlap["classification"] == "owned_symbol"


@pytest.mark.parametrize(
    ("suffix", "comment_only", "real_code"),
    [
        (
            ".ps1",
            "# ExactToken\n<# multiline\nExactToken\n#>\nWrite-Output stable # ExactToken\n",
            "Write-Output 'ExactToken'\n",
        ),
        (
            ".md",
            "<!-- ExactToken -->\nvisible <!-- multiline\nExactToken\n-->\n",
            "## ExactToken\n",
        ),
        (
            ".css",
            "/* ExactToken */\nbody { color: black; } /* multiline\nExactToken\n*/\n",
            ".ExactToken { color: black; }\n",
        ),
    ],
)
def test_non_python_owned_symbol_ignores_language_comments_but_accepts_code(
    tmp_path: Path,
    suffix: str,
    comment_only: str,
    real_code: str,
) -> None:
    repo = _repo(tmp_path)
    source_path = repo / f"owned{suffix}"
    source_path.write_text(comment_only)
    _git(repo, "add", source_path.name)
    _git(repo, "commit", "-m", f"add {suffix} comment fixture")
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    raw = yaml.safe_load(manifest.read_text())
    entry = raw["upstream_changes"][0]
    entry["files"] = [source_path.name]
    entry["owned_symbols"] = ["ExactToken"]
    entry["overlap_policy"] = "owned_symbol"
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)

    source_path.write_text(real_code)
    _git(repo, "commit", "-am", f"add {suffix} real code fixture")
    load_and_validate_manifest(manifest, repo, check_git=False)


def _non_python_manifest(
    tmp_path: Path,
    suffix: str,
    source: str,
) -> tuple[Path, Path, Path]:
    repo = _repo(tmp_path)
    source_path = repo / f"owned{suffix}"
    source_path.write_text(source)
    _git(repo, "add", source_path.name)
    _git(repo, "commit", "-m", f"add {suffix} scanner fixture")
    manifest = _manifest(repo, _git(repo, "rev-parse", "HEAD"))
    raw = yaml.safe_load(manifest.read_text())
    entry = raw["upstream_changes"][0]
    entry["files"] = [source_path.name]
    entry["owned_symbols"] = ["ExactToken"]
    entry["overlap_policy"] = "owned_symbol"
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))
    return repo, source_path, manifest


def test_powershell_backslash_does_not_escape_comment_boundary(tmp_path: Path) -> None:
    repo, _source, manifest = _non_python_manifest(
        tmp_path,
        ".ps1",
        'Write-Output "done\\" # ExactToken\n',
    )

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_powershell_here_string_preserves_hash_token_after_ordinary_quote(
    tmp_path: Path,
) -> None:
    repo, _source, manifest = _non_python_manifest(
        tmp_path,
        ".ps1",
        "$value = @'\nordinary ' quote # ExactToken\n'@\n",
    )

    load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    "source",
    [
        "value: |\n  first line\n  ExactToken\n",
        "url: https://example/#ExactToken\n",
    ],
)
def test_yaml_scalars_preserve_exact_string_tokens(tmp_path: Path, source: str) -> None:
    repo, _source, manifest = _non_python_manifest(tmp_path, ".yaml", source)

    load_and_validate_manifest(manifest, repo, check_git=False)


def test_yaml_comment_does_not_satisfy_owned_symbol(tmp_path: Path) -> None:
    repo, _source, manifest = _non_python_manifest(
        tmp_path,
        ".yaml",
        "value: stable # ExactToken\n",
    )

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    "source",
    [
        "value=${input#ExactToken}\n",
        "cat <<'TOKEN_EOF'\nExactToken\nTOKEN_EOF\n",
    ],
)
def test_shell_parameter_and_quoted_heredoc_preserve_tokens(
    tmp_path: Path,
    source: str,
) -> None:
    repo, _source, manifest = _non_python_manifest(tmp_path, ".sh", source)

    load_and_validate_manifest(manifest, repo, check_git=False)


def test_shell_backslash_quoted_heredoc_preserves_token(tmp_path: Path) -> None:
    repo, _source, manifest = _non_python_manifest(
        tmp_path,
        ".sh",
        "cat <<\\EOF\n# ExactToken\nEOF\n",
    )

    load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    "source",
    [
        "printf '%s\\n' '<<EOF'\n# ExactToken\n",
        "value=`printf ok # ExactToken\n`\n",
    ],
)
def test_shell_non_heredoc_and_command_substitution_comments_do_not_own_token(
    tmp_path: Path,
    source: str,
) -> None:
    repo, _source, manifest = _non_python_manifest(tmp_path, ".sh", source)

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_shell_comment_does_not_satisfy_owned_symbol(tmp_path: Path) -> None:
    repo, _source, manifest = _non_python_manifest(
        tmp_path,
        ".sh",
        "printf '%s\\n' stable # ExactToken\n",
    )

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_typescript_template_expression_comment_is_removed(tmp_path: Path) -> None:
    repo, _source, manifest = _non_python_manifest(
        tmp_path,
        ".ts",
        "const value = `prefix ${/* ExactToken */ 1}`\n",
    )

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_typescript_regex_brace_does_not_expose_template_expression_comment(
    tmp_path: Path,
) -> None:
    repo, _source, manifest = _non_python_manifest(
        tmp_path,
        ".ts",
        "const value = `${/}/.test('}') /* ExactToken */}`\n",
    )

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    "source",
    [
        (
            "const value = `${(() => { return /}}/.test('}}') "
            "/* ExactToken */ })()}`\n"
        ),
        (
            "const value = `${(function* () { yield /}}/.test('}}') "
            "/* ExactToken */ })().next()}`\n"
        ),
        (
            "const value = `${(() => { throw /}}/.test('}}') "
            "/* ExactToken */ })()}`\n"
        ),
    ],
)
def test_typescript_keyword_regex_braces_stay_inside_template_expression(
    tmp_path: Path,
    source: str,
) -> None:
    """Treating a keyword-following slash as division exposes comment text."""
    repo, _source, manifest = _non_python_manifest(tmp_path, ".ts", source)

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    "source",
    [
        (
            "const value = `${(() => { if (true) /}}/.test('}}'); "
            "/* ExactToken */ return 1; })()}`\n"
        ),
        (
            "const value = `${(() => { while (false) /}}/.test('}}'); "
            "/* ExactToken */ return 1; })()}`\n"
        ),
        (
            "const value = `${(() => { for (;;) /}}/.test('}}'); "
            "/* ExactToken */ break; })()}`\n"
        ),
        (
            "const value = `${(() => { do /}}/.test('}}'); while (false); "
            "/* ExactToken */ return 1; })()}`\n"
        ),
        (
            "const value = `${(() => { if (false) 1; else /}}/.test('}}'); "
            "/* ExactToken */ return 1; })()}`\n"
        ),
        (
            "const value = `${(() => { const quotient = 8 / 2; "
            "/* ExactToken */ return quotient; })()}`\n"
        ),
    ],
)
def test_typescript_control_statement_regexes_do_not_expose_comments(
    tmp_path: Path,
    source: str,
) -> None:
    """A regex statement body must not let its braces close ``${...}``."""
    repo, _source, manifest = _non_python_manifest(tmp_path, ".ts", source)

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    "source",
    [
        "let i = 4; i++ / 2; /* ExactToken */\n",
        "let i = 4; i-- / 2; /* ExactToken */\n",
        "const obj = { return: 4 }; obj.return / 2; /* ExactToken */\n",
        "const obj = { return: 4 }; obj?.return / 2; /* ExactToken */\n",
        "const items = [4]; items[0] / 2; /* ExactToken */\n",
        "const quotient = (8) / 2; /* ExactToken */\n",
        "const fn = () => 8; fn() / 2; /* ExactToken */\n",
        "const quotient = ({ value: 8 } / 2); /* ExactToken */\n",
        "const quotient = ({ value: 8 }).value / 2; /* ExactToken */\n",
        "let quotient = 8; quotient /= 2; /* ExactToken */\n",
    ],
)
def test_typescript_division_lexical_goals_do_not_expose_comments(
    tmp_path: Path,
    source: str,
) -> None:
    """Expression-ending tokens keep a following slash in division goal."""
    repo, _source, manifest = _non_python_manifest(tmp_path, ".ts", source)

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    "source",
    [
        "const value = `prefix ExactToken`\n",
        "const value = `prefix ${ExactToken}`\n",
    ],
)
def test_typescript_template_text_and_expression_code_preserve_tokens(
    tmp_path: Path,
    source: str,
) -> None:
    repo, _source, manifest = _non_python_manifest(tmp_path, ".ts", source)

    load_and_validate_manifest(manifest, repo, check_git=False)


def test_markdown_tilde_fence_preserves_html_comment_literal(tmp_path: Path) -> None:
    repo, _source, manifest = _non_python_manifest(
        tmp_path,
        ".md",
        "~~~html\n<!-- ExactToken -->\n~~~\n",
    )

    load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    "source",
    [
        "- ~~~html\n  <!-- ExactToken -->\n  ~~~\n",
        "> ~~~html\n> <!-- ExactToken -->\n> ~~~\n",
        "> - ~~~~html\n>   <!-- ExactToken -->\n>   ~~~~\n",
    ],
)
def test_markdown_commonmark_container_fences_preserve_comment_literals(
    tmp_path: Path,
    source: str,
) -> None:
    repo, _source, manifest = _non_python_manifest(tmp_path, ".md", source)

    load_and_validate_manifest(manifest, repo, check_git=False)


def test_markdown_container_fence_closes_only_at_sufficient_width(
    tmp_path: Path,
) -> None:
    repo, source, manifest = _non_python_manifest(
        tmp_path,
        ".md",
        (
            "> ~~~~html\n"
            "> <!-- ExactToken -->\n"
            "> ~~~\n"
            "> <!-- still fenced -->\n"
            ">   ~~~~\n"
            "<!-- ExactToken before -->\n"
        ),
    )
    baseline = _git(repo, "rev-parse", "HEAD")
    entry = load_and_validate_manifest(manifest, repo, check_git=False)[
        "upstream_changes"
    ][0]
    source.write_text(
        (
            "> ~~~~html\n"
            "> <!-- ExactToken -->\n"
            "> ~~~\n"
            "> <!-- still fenced -->\n"
            ">   ~~~~\n"
            "<!-- ExactToken after -->\n"
        )
    )
    _git(repo, "commit", "-am", "change ordinary comment after nested fence")

    report = classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")

    assert report["classification"] == "same_file"


@pytest.mark.parametrize(
    ("opening_prefix", "continuation_prefix"),
    [
        ("1. ", "   "),
        ("10. ", "    "),
        ("100. ", "     "),
        ("- ", "  "),
        ("> 10. ", ">     "),
        ("> - 10. ", ">       "),
    ],
)
def test_markdown_container_fence_uses_relative_ordered_list_indent(
    tmp_path: Path,
    opening_prefix: str,
    continuation_prefix: str,
) -> None:
    """A fenced list item's close is relative to its own container prefix."""
    before = (
        f"{opening_prefix}~~~~html\n"
        f"{continuation_prefix}<!-- ExactToken -->\n"
        f"{continuation_prefix}~~~\n"
        f"{continuation_prefix}<!-- ExactToken -->\n"
        f"{continuation_prefix}~~~~\n"
        "<!-- ExactToken before -->\n"
    )
    after = before.replace("<!-- ExactToken before -->", "<!-- ExactToken after -->")
    repo, source, manifest = _non_python_manifest(tmp_path, ".md", before)
    baseline = _git(repo, "rev-parse", "HEAD")
    entry = load_and_validate_manifest(manifest, repo, check_git=False)[
        "upstream_changes"
    ][0]
    source.write_text(after)
    _git(repo, "commit", "-am", "change comment after container fence")

    report = classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")

    assert report["classification"] == "same_file"
    assert report.get("owned_symbol_changes", []) == []


@pytest.mark.parametrize(
    ("opening_prefix", "continuation_prefix"),
    [
        ("-\t", "\t"),
        ("1.\t", "\t"),
        ("10.\t", "\t"),
        ("100.\t", "\t\t"),
        ("- \t", " \t"),
        (">\t-\t", ">\t\t"),
        ("-\t10.\t", "\t\t"),
    ],
)
def test_markdown_container_fence_uses_visual_tab_columns(
    tmp_path: Path,
    opening_prefix: str,
    continuation_prefix: str,
) -> None:
    """Tabs advance to CommonMark tab stops in nested container prefixes."""
    before = (
        f"{opening_prefix}````html\n"
        f"{continuation_prefix}<!-- ExactToken -->\n"
        f"{continuation_prefix}```\n"
        f"{continuation_prefix}<!-- ExactToken -->\n"
        f"{continuation_prefix}````\n"
        "<!-- ExactToken before -->\n"
    )
    after = before.replace("<!-- ExactToken before -->", "<!-- ExactToken after -->")
    repo, source, manifest = _non_python_manifest(tmp_path, ".md", before)
    baseline = _git(repo, "rev-parse", "HEAD")
    entry = load_and_validate_manifest(manifest, repo, check_git=False)[
        "upstream_changes"
    ][0]
    source.write_text(after)
    _git(repo, "commit", "-am", "change comment after tab-indented fence")

    report = classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")

    assert report["classification"] == "same_file"
    assert report.get("owned_symbol_changes", []) == []


def test_toml_multiline_string_preserves_hash_token_but_comment_does_not(
    tmp_path: Path,
) -> None:
    repo, source, manifest = _non_python_manifest(
        tmp_path,
        ".toml",
        'value = "stable" # ExactToken\n',
    )
    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)

    source.write_text('value = """prefix "quote" #ExactToken\ntail"""\n')
    _git(repo, "commit", "-am", "install TOML multiline literal")
    load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    ("suffix", "source"),
    [
        (".json", '{"value": "Exact\\u0054oken"}\n'),
        (".toml", 'value = "Exact\\u0054oken"\n'),
    ],
)
def test_structured_escaped_string_value_has_owned_span(
    tmp_path: Path,
    suffix: str,
    source: str,
) -> None:
    repo, _source, manifest = _non_python_manifest(tmp_path, suffix, source)

    load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    ("suffix", "before", "after"),
    [
        (
            ".json",
            '{"Exact\\u0054oken": "stable", "other": 1}\n',
            '{"Other": "stable", "other": 1}\n',
        ),
        (
            ".toml",
            '"Exact\\u0054oken" = "stable"\nother = 1\n',
            '"Other" = "stable"\nother = 1\n',
        ),
    ],
)
def test_structured_escaped_key_has_positioned_owned_span(
    tmp_path: Path,
    suffix: str,
    before: str,
    after: str,
) -> None:
    repo, source, manifest = _non_python_manifest(tmp_path, suffix, before)
    baseline = _git(repo, "rev-parse", "HEAD")
    entry = load_and_validate_manifest(manifest, repo, check_git=False)[
        "upstream_changes"
    ][0]
    source.write_text(after)
    _git(repo, "commit", "-am", "change escaped structured key")

    report = classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")

    assert report["classification"] == "owned_symbol"


@pytest.mark.parametrize(
    ("suffix", "before", "after"),
    [
        (
            ".json",
            '{\n  "first": "Exact\\u0054oken",\n  "second": "ExactToken"\n}\n',
            '{\n  "first": "Exact\\u0054oken",\n  "second": "Other"\n}\n',
        ),
        (
            ".toml",
            'first = "Exact\\u0054oken"\nsecond = "ExactToken"\n',
            'first = "Exact\\u0054oken"\nsecond = "Other"\n',
        ),
    ],
)
def test_duplicate_decoded_structured_strings_each_retain_their_span(
    tmp_path: Path,
    suffix: str,
    before: str,
    after: str,
) -> None:
    repo, source, manifest = _non_python_manifest(tmp_path, suffix, before)
    baseline = _git(repo, "rev-parse", "HEAD")
    entry = load_and_validate_manifest(manifest, repo, check_git=False)[
        "upstream_changes"
    ][0]
    source.write_text(after)
    _git(repo, "commit", "-am", "change second decoded string")

    report = classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")

    assert report["classification"] == "owned_symbol"


@pytest.mark.parametrize(
    ("suffix", "before", "after", "symbol"),
    [
        (
            ".json",
            '{\n  "string": "123",\n  "number": 123\n}\n',
            '{\n  "string": "123",\n  "number": 456\n}\n',
            "123",
        ),
        (
            ".toml",
            'string = "true"\nboolean = true\n',
            'string = "true"\nboolean = false\n',
            "true",
        ),
    ],
)
def test_structured_non_string_scalar_does_not_inherit_string_span(
    tmp_path: Path,
    suffix: str,
    before: str,
    after: str,
    symbol: str,
) -> None:
    repo, source, manifest = _non_python_manifest(tmp_path, suffix, before)
    data = yaml.safe_load(manifest.read_text())
    data["upstream_changes"][0]["owned_symbols"] = [symbol]
    manifest.write_text(yaml.safe_dump(data, sort_keys=False))
    baseline = _git(repo, "rev-parse", "HEAD")
    entry = load_and_validate_manifest(manifest, repo, check_git=False)[
        "upstream_changes"
    ][0]
    source.write_text(after)
    _git(repo, "commit", "-am", "change only non-string structured scalar")

    report = classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")

    assert report["classification"] == "same_file"


@pytest.mark.parametrize(
    ("suffix", "before", "after", "expected"),
    [
        (
            ".json",
            (
                '{"owned":"ExactToken","number":1,"boolean":true,'
                '"null":null,"array":[1,2],"date":"2026-07-27"}\r\n'
            ),
            (
                '{"owned":"ExactToken","number":2,"boolean":false,'
                '"null":null,"array":[2,1],"date":"2026-07-28"}\r\n'
            ),
            "same_file",
        ),
        (
            ".toml",
            (
                'values = { owned = "ExactToken", number = 1, enabled = true, '
                'date = 2026-07-27, array = [1, 2] }\r\n'
            ),
            (
                'values = { owned = "ExactToken", number = 2, enabled = false, '
                'date = 2026-07-28, array = [2, 1] }\r\n'
            ),
            "same_file",
        ),
        (
            ".json",
            '{"owned":"ExactToken","number":1}\n',
            '{"owned":"Other","number":1}\n',
            "owned_symbol",
        ),
        (
            ".toml",
            'ExactToken = "stable"\nnumber = 1\n',
            'Other = "stable"\nnumber = 1\n',
            "owned_symbol",
        ),
        (
            ".json",
            '{"owned":"ExactToken","left":1,"right":2}\n',
            '{"owned":"ExactToken","right":2,"left":1}\n',
            "same_file",
        ),
        (
            ".toml",
            'values = { owned = "ExactToken", left = 1, right = 2 }\n',
            'values = { owned = "ExactToken", right = 2, left = 1 }\n',
            "same_file",
        ),
        (
            ".json",
            '{"owned":"ExactToken","count":1}\n',
            '{"added":0,"owned":"ExactToken","count":1}\n',
            "same_file",
        ),
        (
            ".toml",
            'values = { owned = "ExactToken", count = 1 }\n',
            'values = { added = 0, owned = "ExactToken", count = 1 }\n',
            "same_file",
        ),
        (
            ".json",
            '{"owned":"ExactToken","count":1,"drop":0}\n',
            '{"owned":"ExactToken","count":1}\n',
            "same_file",
        ),
        (
            ".toml",
            'values = { owned = "ExactToken", count = 1, drop = 0 }\n',
            'values = { owned = "ExactToken", count = 1 }\n',
            "same_file",
        ),
    ],
)
def test_structured_same_line_changes_only_match_owned_lexical_tokens(
    tmp_path: Path,
    suffix: str,
    before: str,
    after: str,
    expected: str,
) -> None:
    """Adjacent scalar changes must not inherit an owned string's line span."""
    repo, source, manifest = _non_python_manifest(tmp_path, suffix, before)
    baseline = _git(repo, "rev-parse", "HEAD")
    entry = load_and_validate_manifest(manifest, repo, check_git=False)[
        "upstream_changes"
    ][0]
    source.write_text(after, newline="")
    _git(repo, "commit", "-am", "change structured same-line neighbors")

    report = classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")

    assert report["classification"] == expected


def test_toml_multiline_string_span_covers_each_owned_scalar_line(
    tmp_path: Path,
) -> None:
    repo, source, manifest = _non_python_manifest(
        tmp_path,
        ".toml",
        'value = """ExactToken\nbefore\n"""\nother = true\n',
    )
    baseline = _git(repo, "rev-parse", "HEAD")
    entry = load_and_validate_manifest(manifest, repo, check_git=False)[
        "upstream_changes"
    ][0]
    source.write_text('value = """ExactToken\nafter\n"""\nother = true\n')
    _git(repo, "commit", "-am", "change multiline TOML scalar body")

    report = classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")

    assert report["classification"] == "owned_symbol"


def test_toml_multiline_closing_quote_run_stops_span_before_following_comment(
    tmp_path: Path,
) -> None:
    repo, source, manifest = _non_python_manifest(
        tmp_path,
        ".toml",
        'value = """ExactToken""""\n# ExactToken before\nother = "stable"\n',
    )
    baseline = _git(repo, "rev-parse", "HEAD")
    entry = load_and_validate_manifest(manifest, repo, check_git=False)[
        "upstream_changes"
    ][0]
    source.write_text(
        'value = """ExactToken""""\n# ExactToken after\nother = "stable"\n'
    )
    _git(repo, "commit", "-am", "change comment after multiline TOML string")

    report = classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")

    assert report["classification"] == "same_file"
    assert report.get("owned_symbol_changes", []) == []


def test_yaml_block_scalar_span_stops_before_following_comment(tmp_path: Path) -> None:
    repo, source, manifest = _non_python_manifest(
        tmp_path,
        ".yaml",
        "value: |\n  ExactToken\n# before\nother: stable\n",
    )
    baseline = _git(repo, "rev-parse", "HEAD")
    entry = load_and_validate_manifest(manifest, repo, check_git=False)[
        "upstream_changes"
    ][0]
    source.write_text("value: |\n  ExactToken\n# after\nother: stable\n")
    _git(repo, "commit", "-am", "change comment after owned scalar")

    report = classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")

    assert report["classification"] == "same_file"
    assert report.get("owned_symbol_changes", []) == []


def test_cyclic_yaml_alias_is_traversed_once_and_comment_stays_unowned(
    tmp_path: Path,
) -> None:
    repo, _source, manifest = _non_python_manifest(
        tmp_path,
        ".yaml",
        "root: &root\n  - *root\n# ExactToken\n",
    )

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_json_parser_preserves_values_and_rejects_malformed_input(tmp_path: Path) -> None:
    repo, source, manifest = _non_python_manifest(
        tmp_path,
        ".json",
        '{"value": "ExactToken"}\n',
    )
    load_and_validate_manifest(manifest, repo, check_git=False)

    source.write_text('{"value": }\n')
    _git(repo, "commit", "-am", "install malformed JSON")
    with pytest.raises(ValueError, match="cannot parse .*JSON"):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_overlap_reporting_is_read_only_for_git_and_baseline(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    before_branch = _git(repo, "branch", "--show-current")
    before_text = manifest.read_text()
    before_head = _git(repo, "rev-parse", "HEAD")

    entry = load_and_validate_manifest(manifest, repo)["upstream_changes"][0]
    report = classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")
    json.dumps(report)

    assert manifest.read_text() == before_text
    assert _git(repo, "branch", "--show-current") == before_branch
    assert _git(repo, "rev-parse", "HEAD") == before_head


def test_diff_coverage_enforces_expected_commit_boundary(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    data = load_and_validate_manifest(manifest, repo)
    (repo / "core.py").write_text("class Owned:\n    value = 1\n")
    _git(repo, "commit", "-am", "wrong subject")

    with pytest.raises(ValueError, match="expected commit subject"):
        validate_diff_coverage(data, repo, f"{baseline}..HEAD")

    (repo / "core.py").write_text("class Owned:\n    value = 2\n")
    _git(repo, "commit", "-am", "feat: owned")
    validate_diff_coverage(data, repo, f"{baseline}..HEAD")


def test_manifest_coverage_scope_excludes_pre_feature_and_named_release_commits(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    root = _git(repo, "rev-parse", "HEAD")
    (repo / "preexisting_fork.py").write_text("fork = True\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "pre-existing fork customization")
    feature_base = _git(repo, "rev-parse", "HEAD")
    (repo / "release_only.py").write_text("version = 'alpha'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "separate alpha release")
    excluded = _git(repo, "rev-parse", "HEAD")
    (repo / "core.py").write_text("class Owned:\n    value = 1\n")
    _git(repo, "commit", "-am", "feat: owned")

    manifest = _manifest(repo, root)
    raw = yaml.safe_load(manifest.read_text())
    raw["coverage"] = {
        "base_commit": feature_base,
        "excluded_commits": [
            {"commit": excluded, "reason": "separate user-requested alpha release"}
        ],
    }
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))
    data = load_and_validate_manifest(manifest, repo)

    validate_diff_coverage(data, repo, f"{feature_base}..HEAD")

    (repo / "unledgered_feature.py").write_text("value = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feature scope leak")
    with pytest.raises(ValueError, match="unledgered_feature.py"):
        validate_diff_coverage(data, repo, f"{feature_base}..HEAD")


def test_diff_coverage_honors_requested_left_revision(tmp_path: Path) -> None:
    """A narrow caller range must not inherit unrelated older ledger debt."""
    repo = _repo(tmp_path)
    coverage_base = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, coverage_base)
    raw = yaml.safe_load(manifest.read_text())
    raw["coverage"] = {"base_commit": coverage_base, "excluded_commits": []}
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))

    (repo / "older_unrelated.py").write_text("value = 'outside requested range'\n")
    _git(repo, "add", "older_unrelated.py")
    _git(repo, "commit", "-m", "older unrelated customization")
    requested_left = _git(repo, "rev-parse", "HEAD")

    (repo / "core.py").write_text("class Owned:\n    value = 1\n")
    _git(repo, "commit", "-am", "feat: owned")
    data = load_and_validate_manifest(manifest, repo)

    validate_diff_coverage(data, repo, f"{requested_left}..HEAD")


def test_diff_coverage_accepts_exact_divergent_two_dot_range(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    raw = yaml.safe_load(manifest.read_text())
    raw["coverage"] = {"base_commit": baseline, "excluded_commits": []}
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))

    _git(repo, "checkout", "-b", "left")
    (repo / "left_only.py").write_text("LEFT = True\n")
    _git(repo, "add", "left_only.py")
    _git(repo, "commit", "-m", "left-only history")
    left = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-b", "right", baseline)
    (repo / "core.py").write_text("class Owned:\n    value = 'right'\n")
    _git(repo, "commit", "-am", "feat: owned")
    right = _git(repo, "rev-parse", "HEAD")
    data = load_and_validate_manifest(manifest, repo)

    validate_diff_coverage(data, repo, f"{left}..{right}")


def test_diff_coverage_handles_merge_commit_inside_requested_range(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    raw = yaml.safe_load(manifest.read_text())
    raw["coverage"] = {"base_commit": baseline, "excluded_commits": []}
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))
    primary = _git(repo, "branch", "--show-current")

    _git(repo, "checkout", "-b", "owned-side")
    (repo / "core.py").write_text("class Owned:\n    merged = True\n")
    _git(repo, "commit", "-am", "feat: owned")
    _git(repo, "checkout", primary)
    (repo / "docs").mkdir()
    (repo / "docs/main.md").write_text("main line\n")
    _git(repo, "add", "docs/main.md")
    _git(repo, "commit", "-m", "docs: main line")
    _git(repo, "merge", "--no-ff", "owned-side", "-m", "merge owned side")
    merged = _git(repo, "rev-parse", "HEAD")
    data = load_and_validate_manifest(manifest, repo)

    validate_diff_coverage(data, repo, f"{baseline}..{merged}")


def test_diff_coverage_fails_honestly_for_malformed_revision(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    raw = yaml.safe_load(manifest.read_text())
    raw["coverage"] = {"base_commit": baseline, "excluded_commits": []}
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))
    data = load_and_validate_manifest(manifest, repo)

    with pytest.raises(ValueError, match="range left is not a local commit"):
        validate_diff_coverage(data, repo, "definitely-missing..HEAD")


def test_diff_coverage_applies_exclusions_only_inside_exact_range(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    (repo / "outside.py").write_text("OUTSIDE = True\n")
    _git(repo, "add", "outside.py")
    _git(repo, "commit", "-m", "outside caller range")
    requested_left = _git(repo, "rev-parse", "HEAD")
    _git(repo, "commit", "--allow-empty", "-m", "release-only inside range")
    excluded_inside = _git(repo, "rev-parse", "HEAD")
    (repo / "core.py").write_text("class Owned:\n    value = 1\n")
    _git(repo, "commit", "-am", "feat: owned")

    manifest = _manifest(repo, baseline)
    raw = yaml.safe_load(manifest.read_text())
    raw["coverage"] = {
        "base_commit": baseline,
        "excluded_commits": [
            {"commit": baseline, "reason": "outside exact range"},
            {"commit": excluded_inside, "reason": "release-only inside range"},
        ],
    }
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))
    data = load_and_validate_manifest(manifest, repo)

    validate_diff_coverage(data, repo, f"{requested_left}..HEAD")


def test_manifest_coverage_ignores_only_local_sdd_progress_ledger(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    raw = yaml.safe_load(manifest.read_text())
    raw["coverage"] = {"base_commit": baseline, "excluded_commits": []}
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))

    progress = repo / ".superpowers/sdd/progress.md"
    progress.parent.mkdir(parents=True)
    progress.write_text("local progress\n")
    _git(repo, "add", str(progress.relative_to(repo)))
    _git(repo, "commit", "-m", "accidentally track local progress")
    progress.unlink()
    _git(repo, "commit", "-am", "untrack local progress")

    data = load_and_validate_manifest(manifest, repo)
    validate_diff_coverage(data, repo, f"{baseline}..HEAD")

    adjacent = repo / ".superpowers/sdd/unregistered.md"
    adjacent.write_text("must remain covered\n")
    _git(repo, "add", str(adjacent.relative_to(repo)))
    _git(repo, "commit", "-m", "add unregistered sdd artifact")
    with pytest.raises(ValueError, match=r"\.superpowers/sdd/unregistered\.md"):
        validate_diff_coverage(data, repo, f"{baseline}..HEAD")


def test_strict_cli_validates_owned_symbols_at_requested_base_ref(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    (repo / "core.py").write_text("class Replacement:\n    pass\n")
    _git(repo, "commit", "-am", "replace owned definition")
    monkeypatch.chdir(repo)

    assert customization_checker.main(
        [
            "--manifest",
            str(manifest),
            "--strict",
            "--base-ref",
            baseline,
        ]
    ) == 0
    assert customization_checker.main(
        ["--manifest", str(manifest), "--strict", "--base-ref", "HEAD"]
    ) == 1
    assert "does not exist in declared files" in capsys.readouterr().err


def test_strict_requested_revision_uses_its_committed_path_inventory(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    requested = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, requested)
    (repo / "core.py").unlink()
    _git(repo, "commit", "-am", "delete owned file after requested revision")

    load_and_validate_manifest(
        manifest,
        repo,
        source_revision=requested,
        strict=True,
    )

    future = repo / "future.py"
    future.write_text("class FutureOwned:\n    pass\n")
    _git(repo, "add", "future.py")
    _git(repo, "commit", "-m", "add future owned file")
    data = yaml.safe_load(manifest.read_text())
    data["upstream_changes"][0]["files"] = ["future.py"]
    data["upstream_changes"][0]["owned_symbols"] = ["FutureOwned"]
    manifest.write_text(yaml.safe_dump(data, sort_keys=False))

    with pytest.raises(ValueError, match="future.py.*source revision"):
        load_and_validate_manifest(
            manifest,
            repo,
            source_revision=requested,
            strict=True,
        )


def test_strict_requested_revision_ignores_current_checkout_symlink(
    tmp_path: Path,
) -> None:
    """A later checkout symlink must not redefine an older commit's path."""
    repo = _repo(tmp_path)
    requested = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, requested)
    outside = tmp_path / "outside.py"
    outside.write_text("class Owned:\n    pass\n")
    (repo / "core.py").unlink()
    (repo / "core.py").symlink_to(outside)
    _git(repo, "add", "core.py")
    _git(repo, "commit", "-m", "replace owned path with later symlink")

    load_and_validate_manifest(
        manifest,
        repo,
        source_revision=requested,
        strict=True,
    )


def test_strict_requested_revision_rejects_committed_symlink_even_when_current_is_regular(
    tmp_path: Path,
) -> None:
    """A symlink at the requested tree cannot borrow a later regular file."""
    repo = _repo(tmp_path)
    target = repo / "owned-target.py"
    target.write_text("class Owned:\n    pass\n")
    (repo / "core.py").unlink()
    (repo / "core.py").symlink_to(target.name)
    _git(repo, "add", "core.py", target.name)
    _git(repo, "commit", "-m", "install historical symlink")
    requested = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, requested)
    (repo / "core.py").unlink()
    (repo / "core.py").write_text("class Owned:\n    pass\n")
    _git(repo, "add", "core.py")
    _git(repo, "commit", "-m", "replace historical symlink with regular file")

    with pytest.raises(ValueError, match="core.py.*regular file.*source revision"):
        load_and_validate_manifest(
            manifest,
            repo,
            source_revision=requested,
            strict=True,
        )


def test_strict_requested_revision_rejects_newer_verified_baseline(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    requested = _git(repo, "rev-parse", "HEAD")
    _git(repo, "commit", "--allow-empty", "-m", "newer verification point")
    newer = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, newer)

    with pytest.raises(ValueError, match="baseline is not an ancestor of source revision"):
        load_and_validate_manifest(
            manifest,
            repo,
            source_revision=requested,
            strict=True,
        )


def test_strict_requested_revision_rejects_newer_coverage_base(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    requested = _git(repo, "rev-parse", "HEAD")
    _git(repo, "commit", "--allow-empty", "-m", "future coverage point")
    newer = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, requested)
    data = yaml.safe_load(manifest.read_text())
    data["coverage"] = {"base_commit": newer, "excluded_commits": []}
    manifest.write_text(yaml.safe_dump(data, sort_keys=False))

    with pytest.raises(ValueError, match="coverage base is not an ancestor of source revision"):
        load_and_validate_manifest(
            manifest,
            repo,
            source_revision=requested,
            strict=True,
        )
