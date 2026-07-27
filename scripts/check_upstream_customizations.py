#!/usr/bin/env python3
"""Validate and compare Hermes' machine-readable customization ledger."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tomllib
from typing import Any

import yaml


_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_MAX_EVIDENCE_ID_LENGTH = 512
_MAX_REPOSITORY_PATH_LENGTH = 4096
_MAX_OWNED_SYMBOL_LENGTH = 256
_MAX_OWNED_INVARIANT_LENGTH = 512
_MAX_OWNED_INVARIANTS = 128
_MAX_SCANNED_SOURCE_BYTES = 4 * 1024 * 1024
_OVERLAP_POLICIES = frozenset({"owned_symbol", "any_owned_file"})
_MACHINE_SYMBOL = re.compile(r"^\S+$")
_EPHEMERAL_COVERAGE_PATHS = frozenset({".superpowers/sdd/progress.md"})
_REQUIRED = {
    "id", "change_class", "owner", "files", "owned_symbols", "tests",
    "expected_commit_subject", "upstream_candidate", "merge_guidance",
    "removal_condition", "last_verified_upstream",
}


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=False
    )
    if check and proc.returncode:
        raise ValueError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def _resolve_commit(repo: Path, revision: str, label: str) -> str:
    if not revision or ".." in revision:
        raise ValueError(f"{label} is not a local commit: {revision or '<empty>'}")
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode:
        raise ValueError(f"{label} is not a local commit: {revision}")
    return proc.stdout.strip()


def _resolved_diff_endpoints(repo: Path, diff_range: str) -> tuple[str, str]:
    if "..." in diff_range:
        if diff_range.count("...") != 1:
            raise ValueError(f"malformed diff range: {diff_range}")
        left_raw, right_raw = diff_range.split("...", 1)
        left_tip = _resolve_commit(repo, left_raw, "range left")
        right = _resolve_commit(repo, right_raw, "range right")
        proc = subprocess.run(
            ["git", "merge-base", left_tip, right],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode or not proc.stdout.strip():
            raise ValueError("triple-dot range has no merge base")
        return proc.stdout.strip(), right
    if ".." in diff_range:
        if diff_range.count("..") != 1:
            raise ValueError(f"malformed diff range: {diff_range}")
        left_raw, right_raw = diff_range.split("..", 1)
        return (
            _resolve_commit(repo, left_raw, "range left"),
            _resolve_commit(repo, right_raw, "range right"),
        )
    right = _resolve_commit(repo, diff_range, "range right")
    return _resolve_commit(repo, f"{right}^", "range left"), right


def _blob_text(repo: Path, revision: str, path: str) -> str:
    return _git(repo, "show", f"{revision}:{path}", check=False)


def _is_shallow_clone(repo: Path) -> bool:
    """True when this clone lacks the history the commit assertions need.

    ``actions/checkout`` fetches depth 1 by default, so neither
    ``coverage.base_commit`` nor any entry's ``last_verified_upstream`` exists
    locally in CI. Those assertions then fail for a reason that has nothing to
    do with the ledger being wrong -- the first CI run on this fork's
    development branch reported "coverage base is not a local commit" and took
    the whole merge gate down with it.

    Deepening the checkout was the alternative and was rejected: the pack is
    ~400 MiB and eight parallel test slices would each pay a full-history fetch
    to validate two pinned commits. Skipping only the history-dependent
    assertions keeps every schema, path, and field check running everywhere,
    and the skipped ones still run on any full clone -- a developer's checkout
    and the merge skill, which are the contexts that actually gate a merge.
    """
    proc = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip() == "true"


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        capture_output=True,
    ).returncode == 0


def _contained(repo: Path, raw: str) -> Path:
    path = (repo / raw).resolve()
    try:
        path.relative_to(repo.resolve())
    except ValueError as exc:
        raise ValueError(f"ledger path is not repository-contained: {raw}") from exc
    return path


def load_and_validate_manifest(
    manifest_path: Path,
    repo: Path,
    *,
    check_git: bool = True,
    source_revision: str = "HEAD",
    strict: bool = False,
) -> dict[str, Any]:
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("manifest schema_version must be 1")
    # Resolved once: the commit-existence and ancestry assertions below are
    # unevaluable without full history. See _is_shallow_clone.
    shallow = check_git and _is_shallow_clone(repo)
    if shallow and strict:
        raise ValueError("strict validation requires complete local Git history")
    if shallow:
        print(
            f"note: {manifest_path.name}: shallow clone -- skipping commit-history "
            "assertions (schema, paths and fields are still enforced)",
            file=sys.stderr,
        )
    entries = data.get("upstream_changes")
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest upstream_changes must be a non-empty list")
    resolved_source_revision = _resolve_commit(repo, source_revision, "source revision")
    source_revision_files: dict[str, str] = {}

    def revision_source(path: str) -> str:
        if path not in source_revision_files:
            source_revision_files[path] = _blob_text(
                repo, resolved_source_revision, path
            )
        return source_revision_files[path]

    coverage = data.get("coverage")
    if coverage is not None:
        if not isinstance(coverage, dict):
            raise ValueError("manifest coverage must be a mapping")
        base_commit = coverage.get("base_commit")
        if not isinstance(base_commit, str) or not _HEX40.fullmatch(base_commit):
            raise ValueError("coverage.base_commit must be exact 40-hex")
        exclusions = coverage.get("excluded_commits", [])
        if not isinstance(exclusions, list):
            raise ValueError("coverage.excluded_commits must be a list")
        excluded_shas: set[str] = set()
        for exclusion in exclusions:
            if not isinstance(exclusion, dict):
                raise ValueError("every excluded commit must be a mapping")
            sha = exclusion.get("commit")
            reason = exclusion.get("reason")
            if not isinstance(sha, str) or not _HEX40.fullmatch(sha):
                raise ValueError("excluded commit must be exact 40-hex")
            if sha in excluded_shas:
                raise ValueError(f"duplicate excluded commit: {sha}")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"excluded commit {sha} must have a non-empty reason")
            excluded_shas.add(sha)
        if check_git and not shallow:
            coverage_base = _resolve_commit(
                repo, coverage["base_commit"], "coverage base"
            )
            if not _is_ancestor(repo, coverage_base, resolved_source_revision):
                raise ValueError(
                    "coverage base is not an ancestor of source revision"
                )
            _validate_coverage_commits(
                coverage, repo, resolved_source_revision
            )
    ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("every upstream change must be a mapping")
        missing = sorted(_REQUIRED - set(entry))
        if missing:
            raise ValueError(f"entry is missing required fields: {', '.join(missing)}")
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id or entry_id in ids:
            raise ValueError(f"duplicate or invalid customization id: {entry_id!r}")
        if len(entry_id) > _MAX_EVIDENCE_ID_LENGTH:
            raise ValueError(
                f"{entry_id[:32]!r}... id must be at most "
                f"{_MAX_EVIDENCE_ID_LENGTH} characters"
            )
        ids.add(entry_id)
        baseline = entry.get("last_verified_upstream")
        if not isinstance(baseline, str) or not _HEX40.fullmatch(baseline):
            raise ValueError(f"{entry_id}.last_verified_upstream must be exact 40-hex")
        for field in ("files", "tests"):
            values = entry.get(field)
            if not isinstance(values, list) or not values:
                raise ValueError(f"{entry_id}.{field} must be a non-empty list")
            for raw in values:
                if not isinstance(raw, str):
                    raise ValueError(f"{entry_id}.{field} paths must be strings")
                if len(raw) > _MAX_REPOSITORY_PATH_LENGTH:
                    raise ValueError(
                        f"{entry_id}.{field} path must be at most "
                        f"{_MAX_REPOSITORY_PATH_LENGTH} characters"
                    )
                portable = PurePosixPath(raw)
                if (
                    portable.is_absolute()
                    or ".." in portable.parts
                    or portable.as_posix() != raw
                ):
                    raise ValueError(
                        f"{entry_id}.{field} path must be normalized "
                        f"repository-relative POSIX: {raw}"
                    )
                path = _contained(repo, raw)
                if strict:
                    if not _existed_at(repo, resolved_source_revision, raw):
                        raise ValueError(
                            f"ledger path {raw} does not exist at source revision"
                        )
                elif not path.is_file():
                    raise ValueError(f"ledger path does not exist: {raw}")
        symbols = entry.get("owned_symbols")
        if not isinstance(symbols, list) or not symbols or not all(
            isinstance(value, str) and value for value in symbols
        ):
            raise ValueError(f"{entry_id}.owned_symbols must contain names")
        overlap_policy = entry.get("overlap_policy", "owned_symbol")
        if not isinstance(overlap_policy, str):
            raise ValueError(f"{entry_id}.overlap_policy must be a string")
        if overlap_policy not in _OVERLAP_POLICIES:
            raise ValueError(
                f"{entry_id}.overlap_policy must be owned_symbol or any_owned_file"
            )
        invariants = entry.get("owned_invariants", [])
        if not isinstance(invariants, list):
            raise ValueError(f"{entry_id}.owned_invariants must be a list")
        if len(invariants) > _MAX_OWNED_INVARIANTS:
            raise ValueError(
                f"{entry_id}.owned_invariants must be a list with at most "
                f"{_MAX_OWNED_INVARIANTS} items"
            )
        if not all(
            isinstance(value, str)
            and bool(value.strip())
            and len(value) <= _MAX_OWNED_INVARIANT_LENGTH
            for value in invariants
        ):
            raise ValueError(
                f"{entry_id}.owned_invariants must contain bounded non-empty prose"
            )
        strict_symbols = _strict_owned_symbols(entry)
        if "overlap_policy" in entry and len(strict_symbols) != len(symbols):
            phrase = next(symbol for symbol in symbols if symbol not in strict_symbols)
            raise ValueError(
                f"{entry_id}.owned_symbols must contain exact machine-locatable "
                f"identifiers; move prose to owned_invariants: {phrase!r}"
            )
        for symbol in strict_symbols:
            if not any(
                _source_contains_symbol(
                    revision_source(raw),
                    symbol,
                    path=raw,
                )
                for raw in entry["files"]
            ):
                raise ValueError(
                    f"{entry_id} owned symbol {symbol!r} does not exist in "
                    "declared files at source revision"
                )
        for field in ("expected_commit_subject", "merge_guidance", "removal_condition"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise ValueError(f"{entry_id}.{field} must be non-empty")
        if check_git and not shallow:
            if not _git(repo, "cat-file", "-e", f"{baseline}^{{commit}}", check=False):
                # cat-file is quiet on success and failure, so inspect status separately.
                proc = subprocess.run(
                    ["git", "cat-file", "-e", f"{baseline}^{{commit}}"], cwd=repo
                )
                if proc.returncode:
                    raise ValueError(f"{entry_id} baseline is not a local commit")
            if not _is_ancestor(repo, baseline, resolved_source_revision):
                raise ValueError(
                    f"{entry_id} baseline is not an ancestor of source revision"
                )
    return data


def _changed_paths(repo: Path, diff_range: str) -> list[tuple[str, list[str]]]:
    rows = []
    for line in _git(repo, "diff", "--name-status", "-M", diff_range).splitlines():
        parts = line.split("\t")
        status = parts[0]
        rows.append((status, parts[1:]))
    return rows


def _existed_at(repo: Path, revision: str, path: str) -> bool:
    return subprocess.run(
        ["git", "cat-file", "-e", f"{revision}:{path}"],
        cwd=repo,
        capture_output=True,
    ).returncode == 0


def _validate_coverage_commits(
    coverage: dict[str, Any], repo: Path, right: str, *, left: str | None = None
) -> list[str]:
    base = _resolve_commit(repo, left or coverage["base_commit"], "range left")
    right = _resolve_commit(repo, right, "range right")
    commits = _git(repo, "rev-list", "--reverse", f"{base}..{right}").splitlines()
    in_range = set(commits)
    excluded = {item["commit"] for item in coverage.get("excluded_commits", [])}
    return [commit for commit in commits if commit not in (excluded & in_range)]


def _coverage_changes(
    data: dict[str, Any], repo: Path, diff_range: str
) -> tuple[list[tuple[str, list[str]]], list[str]] | None:
    coverage = data.get("coverage")
    if coverage is None:
        return None
    left, right = _resolved_diff_endpoints(repo, diff_range)
    commits = _validate_coverage_commits(coverage, repo, right, left=left)
    changes: list[tuple[str, list[str]]] = []
    for commit in commits:
        changes.extend(_changed_paths(repo, f"{commit}^..{commit}"))
    return changes, commits


def validate_diff_coverage(data: dict[str, Any], repo: Path, diff_range: str) -> None:
    range_left, range_right = _resolved_diff_endpoints(repo, diff_range)
    resolved_range = f"{range_left}..{range_right}"
    covered = {
        path
        for entry in data["upstream_changes"]
        for field in ("files", "tests")
        for path in entry[field]
    }
    always_ignored_prefixes = (
        "docs/", "tests/", "skills/", "optional-skills/", "capabilities/", "brands/",
    )
    additive_prefixes = ("plugins/", "scripts/")
    scoped = _coverage_changes(data, repo, diff_range)
    changes = scoped[0] if scoped is not None else _changed_paths(repo, resolved_range)
    baseline = range_left
    missing: set[str] = set()
    for _status, paths in changes:
        for path in paths:
            name = Path(path).name
            is_colocated_test = (
                "/__tests__/" in f"/{path}"
                or ".test." in name
                or ".spec." in name
            )
            if (
                path in covered
                or path in _EPHEMERAL_COVERAGE_PATHS
                or path.startswith(always_ignored_prefixes)
                or (
                    path.startswith(additive_prefixes)
                    and not _existed_at(repo, baseline, path)
                )
                or is_colocated_test
            ):
                continue
            missing.add(path)
    if missing:
        raise ValueError(
            "upstream-owned files missing from customization ledger: "
            + ", ".join(sorted(missing))
        )
    changed = {path for _status, paths in changes for path in paths}
    dirty = {
        line[3:] for line in _git(repo, "status", "--porcelain").splitlines()
        if len(line) > 3
    }
    for entry in data["upstream_changes"]:
        touched = changed & set(entry["files"])
        if not touched or touched & dirty:
            # A planned boundary may be validated before its commit; the exact
            # subject becomes mandatory as soon as the files are clean.
            continue
        if scoped is None:
            subjects = _git(
                repo,
                "log",
                "--format=%s",
                resolved_range,
                "--",
                *sorted(touched),
            ).splitlines()
        else:
            subjects = [
                _git(repo, "show", "-s", "--format=%s", commit).strip()
                for commit in scoped[1]
                if set(
                    path
                    for _status, paths in _changed_paths(repo, f"{commit}^..{commit}")
                    for path in paths
                )
                & touched
            ]
        expected = entry["expected_commit_subject"]
        if expected not in subjects:
            raise ValueError(
                f"{entry['id']} is not isolated in expected commit subject: {expected}"
            )


class _PythonSymbolCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope: list[str] = []
        self.spans: dict[str, list[tuple[int, int]]] = {}

    def _record(self, symbol: str, node: ast.AST) -> None:
        if not hasattr(node, "lineno"):
            return
        start = node.lineno
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            start = min(
                [start, *(decorator.lineno for decorator in node.decorator_list)]
            )
        self.spans.setdefault(symbol, []).append(
            (start, getattr(node, "end_lineno", node.lineno))
        )

    def _record_scoped(self, name: str, node: ast.AST) -> None:
        self._record(name, node)
        if self.scope:
            self._record(".".join([*self.scope, name]), node)

    def _visit_definition(self, node: ast.AST, name: str) -> None:
        self._record_scoped(name, node)
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_definition(node, node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_definition(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_definition(node, node.name)

    def visit_Name(self, node: ast.Name) -> None:
        self._record(node.id, node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        parts = [node.attr]
        value = node.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
            self._record(".".join(reversed(parts)), node)
            if value.id in {"self", "cls"} and self.scope:
                self._record(".".join([self.scope[0], *reversed(parts[:-1])]), node)
        self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> None:
        self._record(node.arg, node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self._record(node.value, node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._record_assignment_target(target)
            if isinstance(target, ast.Name):
                for child in ast.walk(node.value):
                    if isinstance(child, ast.Constant) and isinstance(child.value, str):
                        self._record(f"{target.id}.{child.value}", child)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record_assignment_target(node.target)
        self.generic_visit(node)

    def _record_assignment_target(self, target: ast.expr) -> None:
        if isinstance(target, ast.Name) and self.scope:
            self._record(".".join([*self.scope, target.id]), target)


def _blank(result: list[str], source: str, start: int, end: int) -> None:
    for index in range(start, end):
        result[index] = "\n" if source[index] == "\n" else " "


def _powershell_without_comments(source: str) -> str:
    """Remove PowerShell comments using PowerShell's backtick escape rules."""
    result = list(source)
    index = 0
    mode = "code"
    while index < len(source):
        if mode in {"here_single", "here_double"}:
            marker = "'@" if mode == "here_single" else '"@'
            line_start = index == 0 or source[index - 1] == "\n"
            if line_start:
                marker_at = index
                while marker_at < len(source) and source[marker_at] in " \t":
                    marker_at += 1
                if source.startswith(marker, marker_at):
                    after = marker_at + len(marker)
                    line_end = source.find("\n", after)
                    line_end = len(source) if line_end < 0 else line_end
                    if not source[after:line_end].strip():
                        mode = "code"
                        index = after
                        continue
            newline = source.find("\n", index)
            index = len(source) if newline < 0 else newline + 1
            continue
        if mode == "block":
            end = source.find("#>", index)
            end = len(source) if end < 0 else end + 2
            _blank(result, source, index, end)
            index = end
            mode = "code"
            continue
        char = source[index]
        if mode == "single":
            if source.startswith("''", index):
                index += 2
            else:
                if char == "'":
                    mode = "code"
                index += 1
            continue
        if mode == "double":
            if char == "`":
                index += min(2, len(source) - index)
            else:
                if char == '"':
                    mode = "code"
                index += 1
            continue
        if source.startswith("<#", index):
            mode = "block"
            continue
        if source.startswith("@'", index) or source.startswith('@"', index):
            line_end = source.find("\n", index + 2)
            line_end = len(source) if line_end < 0 else line_end
            if not source[index + 2 : line_end].strip():
                mode = "here_single" if source[index + 1] == "'" else "here_double"
                index += 2
            else:
                index += 1
        elif char == "#":
            end = source.find("\n", index)
            end = len(source) if end < 0 else end
            _blank(result, source, index, end)
            index = end
        elif char == "`":
            index += min(2, len(source) - index)
        elif char == "'":
            mode = "single"
            index += 1
        elif char == '"':
            mode = "double"
            index += 1
        else:
            index += 1
    return "".join(result)


def _shell_heredoc_at(line: str, index: int) -> tuple[int, str, bool] | None:
    if not line.startswith("<<", index) or line.startswith("<<<", index):
        return None
    cursor = index + 2
    strip_tabs = cursor < len(line) and line[cursor] == "-"
    if strip_tabs:
        cursor += 1
    while cursor < len(line) and line[cursor] in " \t":
        cursor += 1
    delimiter: list[str] = []
    quote = ""
    while cursor < len(line):
        char = line[cursor]
        if quote:
            if char == quote:
                quote = ""
                cursor += 1
            elif quote == '"' and char == "\\" and cursor + 1 < len(line):
                delimiter.append(line[cursor + 1])
                cursor += 2
            else:
                delimiter.append(char)
                cursor += 1
            continue
        if char in "'\"":
            quote = char
            cursor += 1
        elif char == "\\" and cursor + 1 < len(line):
            delimiter.append(line[cursor + 1])
            cursor += 2
        elif char.isspace() or char in ";|&()<>":
            break
        else:
            delimiter.append(char)
            cursor += 1
    if quote or not delimiter:
        return None
    return cursor, "".join(delimiter), strip_tabs


def _shell_without_comments(source: str) -> str:
    """Remove shell comments while retaining parameter trims and here-doc bodies."""
    result = list(source)
    stack: list[tuple[str, str | None]] = [("code", None)]
    heredocs: list[tuple[str, bool]] = []
    offset = 0
    for line in source.splitlines(keepends=True):
        body = line[:-1] if line.endswith("\n") else line
        if heredocs:
            delimiter, strip_tabs = heredocs[0]
            candidate = body.lstrip("\t") if strip_tabs else body
            if candidate == delimiter:
                heredocs.pop(0)
            offset += len(line)
            continue
        index = 0
        comment_at: int | None = None
        while index < len(body):
            mode, closer = stack[-1]
            char = body[index]
            if mode == "single":
                if char == "'":
                    stack.pop()
                index += 1
                continue
            if mode == "double":
                if char == "\\":
                    index += min(2, len(body) - index)
                elif char == '"':
                    stack.pop()
                    index += 1
                elif char == "`":
                    stack.append(("code", "`"))
                    index += 1
                else:
                    index += 1
                continue
            if closer is not None and char == closer:
                stack.pop()
                index += 1
            elif char == "\\":
                index += min(2, len(body) - index)
            elif char == "'":
                stack.append(("single", None))
                index += 1
            elif char == '"':
                stack.append(("double", None))
                index += 1
            elif char == "`":
                stack.append(("code", "`"))
                index += 1
            elif char == "#" and (
                index == 0 or body[index - 1].isspace() or body[index - 1] in ";|&()"
            ):
                comment_at = index
                break
            elif body.startswith("<<", index):
                parsed = _shell_heredoc_at(body, index)
                if parsed is None:
                    index += 1
                else:
                    index, delimiter, strip_tabs = parsed
                    heredocs.append((delimiter, strip_tabs))
            else:
                index += 1
        if comment_at is not None:
            _blank(result, source, offset + comment_at, offset + len(body))
        offset += len(line)
    return "".join(result)


def _typescript_without_comments(source: str) -> str:
    """Remove JS/TS comments, including comments inside template expressions."""
    result = list(source)
    regex_prefix_keywords = frozenset(
        {
            "await",
            "case",
            "delete",
            "in",
            "instanceof",
            "new",
            "of",
            "return",
            "throw",
            "typeof",
            "void",
            "yield",
        }
    )
    # Stack entries are mode, template-expression brace depth, regex eligibility.
    stack: list[tuple[str, int, bool]] = [("code", 0, True)]
    index = 0
    while index < len(source):
        mode, depth, regex_allowed = stack[-1]
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if mode in {"single", "double"}:
            closer = "'" if mode == "single" else '"'
            if char == "\\":
                index += min(2, len(source) - index)
            else:
                if char == closer:
                    stack.pop()
                index += 1
            continue
        if mode == "template":
            if char == "\\":
                index += min(2, len(source) - index)
            elif source.startswith("${", index):
                stack.append(("expression", 1, True))
                index += 2
            elif char == "`":
                stack.pop()
                index += 1
            else:
                index += 1
            continue
        if char == "/" and following == "/":
            end = source.find("\n", index)
            end = len(source) if end < 0 else end
            _blank(result, source, index, end)
            index = end
            continue
        if char == "/" and following == "*":
            end = source.find("*/", index + 2)
            end = len(source) if end < 0 else end + 2
            _blank(result, source, index, end)
            index = end
            continue
        if char == "/" and regex_allowed:
            cursor = index + 1
            in_class = False
            closed = False
            while cursor < len(source):
                current = source[cursor]
                if current == "\\":
                    cursor += min(2, len(source) - cursor)
                elif current == "[":
                    in_class = True
                    cursor += 1
                elif current == "]" and in_class:
                    in_class = False
                    cursor += 1
                elif current == "/" and not in_class:
                    cursor += 1
                    while cursor < len(source) and source[cursor].isalpha():
                        cursor += 1
                    closed = True
                    break
                elif current in "\r\n":
                    break
                else:
                    cursor += 1
            if closed:
                stack[-1] = (mode, depth, False)
                index = cursor
                continue
        if char in {"'", '"'}:
            stack.append(("single" if char == "'" else "double", 0, False))
            index += 1
        elif char == "`":
            stack.append(("template", 0, False))
            index += 1
        elif mode == "expression" and char == "{":
            stack[-1] = (mode, depth + 1, True)
            index += 1
        elif mode == "expression" and char == "}":
            if depth == 1:
                stack.pop()
            else:
                stack[-1] = (mode, depth - 1, False)
            index += 1
        elif char.isspace():
            index += 1
        elif char.isalnum() or char in "_$":
            cursor = index + 1
            while cursor < len(source) and (
                source[cursor].isalnum() or source[cursor] in "_$"
            ):
                cursor += 1
            token = source[index:cursor]
            stack[-1] = (mode, depth, token in regex_prefix_keywords)
            index = cursor
        else:
            stack[-1] = (
                mode,
                depth,
                char not in ").]" and char != ".",
            )
            index += 1
    return "".join(result)


def _css_without_comments(source: str) -> str:
    result = list(source)
    index = 0
    quote = ""
    while index < len(source):
        char = source[index]
        if quote:
            if char == "\\":
                index += min(2, len(source) - index)
            else:
                if char == quote:
                    quote = ""
                index += 1
        elif char in {"'", '"'}:
            quote = char
            index += 1
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            end = len(source) if end < 0 else end + 2
            _blank(result, source, index, end)
            index = end
        else:
            index += 1
    return "".join(result)


def _markdown_container_content(line: str) -> str:
    """Return a line relative to its bounded CommonMark container prefix."""
    cursor = 0
    while cursor < len(line):
        remainder = line[cursor:]
        blockquote = re.match(r" {0,3}>[ \t]?", remainder)
        if blockquote:
            cursor += blockquote.end()
            continue
        list_item = re.match(
            r" {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]+|$)", remainder
        )
        if list_item:
            cursor += list_item.end()
            continue
        break
    return line[cursor:]


def _markdown_without_comments(source: str) -> str:
    result = list(source)
    fence: tuple[str, int] | None = None
    inline_ticks = 0
    html_comment_end = 0
    offset = 0
    for line in source.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        container_content = _markdown_container_content(body)
        fence_match = (
            None
            if html_comment_end > offset
            else re.match(r" {0,3}(`{3,}|~{3,})(.*)$", container_content)
        )
        if fence is not None:
            marker, width = fence
            if re.fullmatch(
                rf" {{0,3}}{re.escape(marker)}{{{width},}}[ \t]*",
                container_content,
            ):
                fence = None
            offset += len(line)
            continue
        if fence_match:
            run = fence_match.group(1)
            if run[0] == "~" or "`" not in fence_match.group(2):
                fence = (run[0], len(run))
                offset += len(line)
                continue
        index = min(len(body), max(0, html_comment_end - offset))
        while index < len(body):
            if body[index] == "`":
                end = index
                while end < len(body) and body[end] == "`":
                    end += 1
                count = end - index
                if inline_ticks == 0:
                    inline_ticks = count
                elif count == inline_ticks:
                    inline_ticks = 0
                index = end
            elif inline_ticks == 0 and body.startswith("<!--", index):
                absolute = offset + index
                end = source.find("-->", absolute + 4)
                end = len(source) if end < 0 else end + 3
                _blank(result, source, absolute, end)
                html_comment_end = end
                index = len(body)
            else:
                index += 1
        offset += len(line)
    return "".join(result)


def _toml_without_comments(source: str) -> str:
    result = list(source)
    index = 0
    mode = "code"
    while index < len(source):
        if mode == "code":
            if source.startswith("'''", index):
                mode = "multiline_literal"
                index += 3
            elif source.startswith('\"\"\"', index):
                mode = "multiline_basic"
                index += 3
            elif source[index] == "'":
                mode = "literal"
                index += 1
            elif source[index] == '"':
                mode = "basic"
                index += 1
            elif source[index] == "#":
                end = source.find("\n", index)
                end = len(source) if end < 0 else end
                _blank(result, source, index, end)
                index = end
            else:
                index += 1
        elif mode == "multiline_literal":
            if source.startswith("'''", index):
                while index < len(source) and source[index] == "'":
                    index += 1
                mode = "code"
            else:
                index += 1
        elif mode == "multiline_basic":
            if source.startswith('\"\"\"', index):
                while index < len(source) and source[index] == '"':
                    index += 1
                mode = "code"
            elif source[index] == "\\":
                index += min(2, len(source) - index)
            else:
                index += 1
        elif mode == "literal":
            if source[index] == "'":
                mode = "code"
            index += 1
        else:
            if source[index] == "\\":
                index += min(2, len(source) - index)
            else:
                if source[index] == '"':
                    mode = "code"
                index += 1
    return "".join(result)


def _semantic_strings(value: Any) -> list[str]:
    strings: list[str] = []
    pending = [value]
    seen_containers: set[int] = set()
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            strings.append(item)
            continue
        if isinstance(item, dict):
            identity = id(item)
            if identity in seen_containers:
                continue
            seen_containers.add(identity)
            for key, child in item.items():
                if isinstance(key, str):
                    strings.append(key)
                pending.append(child)
        elif isinstance(item, (list, tuple)):
            identity = id(item)
            if identity in seen_containers:
                continue
            seen_containers.add(identity)
            pending.extend(item)
    return strings


def _json_string_tokens(source: str) -> list[tuple[str, int, int]]:
    tokens: list[tuple[str, int, int]] = []
    index = 0
    while index < len(source):
        if source[index] != '"':
            index += 1
            continue
        start = index
        index += 1
        while index < len(source):
            if source[index] == "\\":
                index += min(2, len(source) - index)
            elif source[index] == '"':
                index += 1
                break
            else:
                index += 1
        decoded = json.loads(source[start:index])
        start_line = source.count("\n", 0, start) + 1
        end_line = source.count("\n", 0, index) + 1
        tokens.append((decoded, start_line, end_line))
    return tokens


def _toml_string_end(source: str, start: int) -> int:
    quote = source[start]
    multiline = source.startswith(quote * 3, start)
    index = start + (3 if multiline else 1)
    while index < len(source):
        if quote == '"' and source[index] == "\\":
            index += min(2, len(source) - index)
            continue
        if multiline and source.startswith(quote * 3, index):
            while index < len(source) and source[index] == quote:
                index += 1
            return index
        if not multiline and source[index] == quote:
            return index + 1
        index += 1
    return len(source)


def _toml_string_tokens(source: str) -> list[tuple[str, int, int]]:
    tokens: list[tuple[str, int, int]] = []
    index = 0
    while index < len(source):
        if source[index] == "#":
            newline = source.find("\n", index)
            index = len(source) if newline < 0 else newline + 1
            continue
        if source[index] not in "'\"":
            index += 1
            continue
        start = index
        index = _toml_string_end(source, start)
        token = source[start:index]
        decoded = tomllib.loads(f"value = {token}\n")["value"]
        start_line = source.count("\n", 0, start) + 1
        end_line = source.count("\n", 0, index) + 1
        tokens.append((decoded, start_line, end_line))
    return tokens


def _toml_bare_key_tokens(source: str) -> list[tuple[str, int, int]]:
    """Locate TOML bare keys without making scalar values searchable."""
    searchable = list(_toml_without_comments(source))
    index = 0
    while index < len(source):
        if searchable[index] not in "'\"":
            index += 1
            continue
        end = _toml_string_end(source, index)
        _blank(searchable, source, index, end)
        index = end

    tokens: list[tuple[str, int, int]] = []
    offset = 0
    key_expression = r"[A-Za-z0-9_-]+(?:[ \t]*\.[ \t]*[A-Za-z0-9_-]+)*"
    for line in "".join(searchable).splitlines(keepends=True):
        body = line.rstrip("\r\n")
        regions: list[tuple[int, int]] = []
        table = re.match(
            rf"^[ \t]*\[\[?[ \t]*({key_expression})[ \t]*\]\]?[ \t]*$",
            body,
        )
        if table:
            regions.append(table.span(1))
        for assignment in re.finditer(
            rf"(?:^|[{{,]])[ \t]*({key_expression})[ \t]*=",
            body,
        ):
            regions.append(assignment.span(1))
        for start, end in regions:
            for token in re.finditer(r"[A-Za-z0-9_-]+", body[start:end]):
                absolute = offset + start + token.start()
                line_number = source.count("\n", 0, absolute) + 1
                tokens.append((token.group(), line_number, line_number))
        offset += len(line)
    return tokens


def _exact_pattern(symbol: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(symbol)}(?![A-Za-z0-9_$])")


def _parse_error(path: str, kind: str, exc: Exception) -> ValueError:
    detail = " ".join(str(exc).split())[:240]
    return ValueError(f"cannot parse {path} as {kind}: {detail}")


def _structured_spans(
    source: str,
    symbols: list[str],
    *,
    path: str,
    suffix: str,
) -> dict[str, list[tuple[int, int]]]:
    spans = {symbol: [] for symbol in symbols}
    if suffix in {".yaml", ".yml"}:
        try:
            documents = list(yaml.safe_load_all(source))
            nodes = list(yaml.compose_all(source, Loader=yaml.SafeLoader))
        except yaml.YAMLError as exc:
            raise _parse_error(path, "YAML", exc) from exc
        semantic = _semantic_strings(documents)
        for symbol in symbols:
            pattern = _exact_pattern(symbol)
            if not any(pattern.search(value) for value in semantic):
                continue
            for document in nodes:
                if document is None:
                    continue
                for node in _walk_yaml_nodes(document):
                    if (
                        isinstance(node, yaml.nodes.ScalarNode)
                        and node.tag == "tag:yaml.org,2002:str"
                        and pattern.search(node.value)
                    ):
                        end_line = (
                            node.end_mark.line + 1
                            if node.end_mark.column
                            else max(node.start_mark.line + 1, node.end_mark.line)
                        )
                        spans[symbol].append(
                            (node.start_mark.line + 1, end_line)
                        )
        return spans
    if suffix == ".toml":
        try:
            parsed = tomllib.loads(source)
        except tomllib.TOMLDecodeError as exc:
            raise _parse_error(path, "TOML", exc) from exc
        decoded_tokens = [
            *_toml_string_tokens(source),
            *_toml_bare_key_tokens(source),
        ]
    else:
        try:
            parsed = json.loads(source)
        except json.JSONDecodeError as exc:
            raise _parse_error(path, "JSON", exc) from exc
        decoded_tokens = _json_string_tokens(source)
    semantic = _semantic_strings(parsed)
    for symbol in symbols:
        pattern = _exact_pattern(symbol)
        if not any(pattern.search(value) for value in semantic):
            continue
        for value, start_line, end_line in decoded_tokens:
            if pattern.search(value):
                spans[symbol].append((start_line, end_line))
    return spans


def _walk_yaml_nodes(node: yaml.nodes.Node) -> list[yaml.nodes.Node]:
    nodes: list[yaml.nodes.Node] = []
    pending = [node]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        nodes.append(current)
        if isinstance(current, yaml.nodes.MappingNode):
            for key, value in reversed(current.value):
                pending.extend((value, key))
        elif isinstance(current, yaml.nodes.SequenceNode):
            pending.extend(reversed(current.value))
    return nodes


def _searchable_non_python(source: str, suffix: str) -> str:
    if suffix in {".ps1", ".psm1", ".psd1"}:
        return _powershell_without_comments(source)
    if suffix in {".sh", ".bash"}:
        return _shell_without_comments(source)
    if suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}:
        return _typescript_without_comments(source)
    if suffix in {".css", ".scss", ".less"}:
        return _css_without_comments(source)
    if suffix in {".md", ".markdown"}:
        return _markdown_without_comments(source)
    return source


def _symbol_spans(
    source: str,
    symbols: list[str],
    *,
    path: str,
) -> dict[str, list[tuple[int, int]]]:
    spans = {symbol: [] for symbol in symbols}
    if len(source) > _MAX_SCANNED_SOURCE_BYTES or len(
        source.encode("utf-8", errors="replace")
    ) > _MAX_SCANNED_SOURCE_BYTES:
        raise ValueError(
            f"cannot scan {path}: source exceeds {_MAX_SCANNED_SOURCE_BYTES} bytes"
        )
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return spans
        collector = _PythonSymbolCollector()
        collector.visit(tree)
        for symbol in symbols:
            spans[symbol].extend(collector.spans.get(symbol, []))
        return spans
    if suffix in {".yaml", ".yml", ".toml", ".json"}:
        return _structured_spans(source, symbols, path=path, suffix=suffix)
    searchable = _searchable_non_python(source, suffix)
    for symbol in symbols:
        if "." in symbol:
            owner, member = symbol.split(".", 1)
            declaration = re.search(
                rf"\b(?:interface|class|type|namespace)\s+{re.escape(owner)}\b[^{{]{{0,512}}\{{",
                searchable,
            )
            if declaration:
                depth = 1
                cursor = declaration.end()
                while cursor < len(searchable) and depth:
                    depth += (searchable[cursor] == "{") - (searchable[cursor] == "}")
                    cursor += 1
                body = searchable[declaration.end() : cursor - 1]
                member_match = re.search(
                    rf"(?<![A-Za-z0-9_$]){re.escape(member)}(?![A-Za-z0-9_$])",
                    body,
                )
                if member_match:
                    offset = declaration.end() + member_match.start()
                    line = searchable.count("\n", 0, offset) + 1
                    spans[symbol].append((line, line))
        pattern = _exact_pattern(symbol)
        for match in pattern.finditer(searchable):
            line = searchable.count("\n", 0, match.start()) + 1
            spans[symbol].append((line, line))
    return spans


def _source_contains_symbol(source: str, symbol: str, *, path: str) -> bool:
    return bool(_symbol_spans(source, [symbol], path=path)[symbol])


def _strict_owned_symbols(entry: dict[str, Any]) -> list[str]:
    return [
        symbol
        for symbol in entry["owned_symbols"]
        if len(symbol) <= _MAX_OWNED_SYMBOL_LENGTH
        and bool(_MACHINE_SYMBOL.fullmatch(symbol))
    ]


def _changed_line_numbers(diff: str) -> tuple[set[int], set[int]]:
    old_lines: set[int] = set()
    new_lines: set[int] = set()
    old_no = new_no = 0
    for line in diff.splitlines():
        match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if match:
            old_no, new_no = int(match.group(1)), int(match.group(2))
        elif line.startswith("-") and not line.startswith("---"):
            old_lines.add(old_no)
            old_no += 1
        elif line.startswith("+") and not line.startswith("+++"):
            new_lines.add(new_no)
            new_no += 1
        elif not line.startswith("\\"):
            old_no += 1
            new_no += 1
    return old_lines, new_lines


def _owned_symbol_hits(
    entry: dict[str, Any], repo: Path, left: str, right: str, paths: list[str]
) -> list[str]:
    hits: set[str] = set()
    symbols = _strict_owned_symbols(entry)
    for path in paths:
        diff = _git(repo, "diff", "--unified=0", f"{left}..{right}", "--", path)
        old_changed, new_changed = _changed_line_numbers(diff)
        new_source = _blob_text(repo, right, path)
        old_source = _blob_text(repo, left, path)
        for symbol, spans in _symbol_spans(new_source, symbols, path=path).items():
            if any(any(start <= line <= end for line in new_changed) for start, end in spans):
                hits.add(symbol)
        for symbol, spans in _symbol_spans(old_source, symbols, path=path).items():
            if any(any(start <= line <= end for line in old_changed) for start, end in spans):
                hits.add(symbol)
    return sorted(hits)


def classify_upstream_overlap(
    entry: dict[str, Any], repo: Path, diff_range: str
) -> dict[str, Any]:
    left, right = _resolved_diff_endpoints(repo, diff_range)
    changes = _changed_paths(repo, f"{left}..{right}")
    changed = {path for _status, paths in changes for path in paths}
    owned_files = set(entry["files"])
    same_files = sorted(changed & owned_files)
    symbols = _strict_owned_symbols(entry)
    owned_hits = _owned_symbol_hits(entry, repo, left, right, same_files)
    if owned_hits:
        classification = "owned_symbol"
        rationale = f"owned symbols changed: {', '.join(owned_hits)}"
    elif same_files:
        classification = "same_file"
        rationale = f"same ledger-owned file changed: {', '.join(same_files)}"
    else:
        equivalent_hits = _owned_symbol_hits(
            entry,
            repo,
            left,
            right,
            sorted(changed - owned_files),
        )
        if equivalent_hits:
            classification = "possible_upstream_equivalent"
            rationale = f"owned public names appeared elsewhere: {', '.join(equivalent_hits)}"
        else:
            classification = "none"
            rationale = "no file, symbol, or public-contract overlap detected"
    return {
        "id": entry["id"],
        "change_class": entry["change_class"],
        "files": entry["files"],
        "owned_symbols": symbols,
        "owned_invariants": [
            *entry.get("owned_invariants", []),
            *[symbol for symbol in entry["owned_symbols"] if symbol not in symbols],
        ],
        "overlap_policy": entry.get("overlap_policy", "owned_symbol"),
        "expected_commit_subject": entry["expected_commit_subject"],
        "classification": classification,
        "rationale": rationale,
        "merge_guidance": entry["merge_guidance"],
        "removal_condition": entry["removal_condition"],
        "tests": entry["tests"],
        "decision_required": bool(
            classification in {"owned_symbol", "possible_upstream_equivalent"}
            or (
                classification == "same_file"
                and entry.get("overlap_policy", "owned_symbol") == "any_owned_file"
            )
        ),
        "acknowledged": False,
    }


def _set_verified_upstream(path: Path, data: dict[str, Any], repo: Path, sha: str) -> None:
    if not _HEX40.fullmatch(sha):
        raise ValueError("--set-verified-upstream requires exact 40-hex")
    if subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo).returncode:
        raise ValueError("verified upstream is not a local commit")
    if subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=repo).returncode:
        raise ValueError("verified upstream is not an ancestor of HEAD")
    for entry in data["upstream_changes"]:
        entry["last_verified_upstream"] = sha
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("docs/upstream-customizations/workflow-orchestration.yaml"),
    )
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--base-ref", default="HEAD")
    parser.add_argument("--diff")
    parser.add_argument("--upstream-diff")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--set-verified-upstream")
    parser.add_argument("--print-verified-upstream", action="store_true")
    args = parser.parse_args(argv)
    repo = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
    data = yaml.safe_load(args.manifest.read_text(encoding="utf-8"))
    try:
        if args.set_verified_upstream:
            _set_verified_upstream(args.manifest, data, repo, args.set_verified_upstream)
        data = load_and_validate_manifest(
            args.manifest,
            repo,
            source_revision=args.base_ref,
            strict=args.strict,
        )
        if args.print_verified_upstream:
            baselines = {
                entry["last_verified_upstream"]
                for entry in data["upstream_changes"]
            }
            if len(baselines) != 1:
                raise ValueError(
                    "ledger entries do not share one verified upstream baseline"
                )
            print(next(iter(baselines)))
            return 0
        if args.diff:
            validate_diff_coverage(data, repo, args.diff)
        if args.upstream_diff:
            previous: dict[str, Any] = {}
            if args.report and args.report.exists():
                try:
                    previous = json.loads(args.report.read_text(encoding="utf-8"))
                except Exception:
                    previous = {}
            old_ack = {
                item.get("id"): bool(item.get("acknowledged"))
                for item in previous.get("overlaps", [])
                if isinstance(item, dict)
            }
            overlaps = [
                classify_upstream_overlap(entry, repo, args.upstream_diff)
                for entry in data["upstream_changes"]
            ]
            for item in overlaps:
                item["acknowledged"] = old_ack.get(item["id"], False)
            report = {"schema_version": 1, "range": args.upstream_diff, "overlaps": overlaps}
            if args.report:
                args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            else:
                print(json.dumps(report, indent=2))
            if any(item["decision_required"] for item in overlaps):
                return 2
    except ValueError as exc:
        print(f"customization ledger error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
