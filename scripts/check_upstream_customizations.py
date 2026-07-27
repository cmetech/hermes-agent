#!/usr/bin/env python3
"""Validate and compare Hermes' machine-readable customization ledger."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import yaml


_HEX40 = re.compile(r"^[0-9a-f]{40}$")
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


def _contained(repo: Path, raw: str) -> Path:
    path = (repo / raw).resolve()
    try:
        path.relative_to(repo.resolve())
    except ValueError as exc:
        raise ValueError(f"ledger path is not repository-contained: {raw}") from exc
    return path


# An owned_symbol that looks like a code identifier (or dotted path) is checked
# against the entry's own files. Ledgers also use free prose here to name a
# BEHAVIOUR that no single identifier captures ("fire-time AI entitlement
# corroboration"); those contain spaces/punctuation and are skipped.
_SYMBOL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _validate_owned_symbols_exist(repo: Path, entry_id: str, entry: dict) -> None:
    """Fail when a declared identifier-shaped owner is absent from its files.

    Schema validation alone accepted any list of non-empty strings, so the
    ledger could drift out of step with the code in either direction and still
    exit 0 (review finding LOW-008). This catches the drift the checker CAN see:
    a declared owner that no longer exists. The reverse -- an owner silently
    DELETED from the ledger while still load-bearing in code -- is not
    machine-derivable, and is pinned by the fixtures in
    ``tests/scripts/test_check_upstream_customizations.py`` instead.
    """
    blobs: list[str] = []
    for raw in entry.get("files", []):
        path = _contained(repo, raw)
        if path.is_file():
            blobs.append(path.read_text(encoding="utf-8", errors="replace"))
    if not blobs:
        return
    haystack = "\n".join(blobs)
    for symbol in entry.get("owned_symbols", []):
        if not _SYMBOL_IDENTIFIER.match(symbol):
            continue
        # Match the leaf so `Class.method` resolves against a method definition.
        if symbol.split(".")[-1] not in haystack:
            raise ValueError(
                f"{entry_id}.owned_symbols declares {symbol!r}, which appears in "
                "none of the entry's files: either the ledger is stale or the "
                "customization was silently reverted"
            )


def load_and_validate_manifest(
    manifest_path: Path,
    repo: Path,
    *,
    check_git: bool = True,
) -> dict[str, Any]:
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("manifest schema_version must be 1")
    # Resolved once: the commit-existence and ancestry assertions below are
    # unevaluable without full history. See _is_shallow_clone.
    shallow = check_git and _is_shallow_clone(repo)
    if shallow:
        print(
            f"note: {manifest_path.name}: shallow clone -- skipping commit-history "
            "assertions (schema, paths and fields are still enforced)",
            file=sys.stderr,
        )
    entries = data.get("upstream_changes")
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest upstream_changes must be a non-empty list")
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
            _validate_coverage_commits(coverage, repo, "HEAD")
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
                path = _contained(repo, raw)
                if not path.is_file():
                    raise ValueError(f"ledger path does not exist: {raw}")
        for field in ("owned_symbols",):
            if not isinstance(entry.get(field), list) or not all(
                isinstance(value, str) and value for value in entry[field]
            ):
                raise ValueError(f"{entry_id}.{field} must contain names")
        _validate_owned_symbols_exist(repo, entry_id, entry)
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
            proc = subprocess.run(
                ["git", "merge-base", "--is-ancestor", baseline, "HEAD"], cwd=repo
            )
            if proc.returncode:
                raise ValueError(f"{entry_id} baseline is not an ancestor of HEAD")
    return data


def _changed_paths(repo: Path, diff_range: str) -> list[tuple[str, list[str]]]:
    rows = []
    for line in _git(repo, "diff", "--name-status", "-M", diff_range).splitlines():
        parts = line.split("\t")
        status = parts[0]
        rows.append((status, parts[1:]))
    return rows


def _range_right(diff_range: str) -> str:
    if "..." in diff_range:
        return diff_range.split("...", 1)[1]
    if ".." in diff_range:
        return diff_range.split("..", 1)[1]
    return diff_range


def _range_base(data: dict[str, Any], repo: Path, diff_range: str) -> str:
    coverage = data.get("coverage")
    if isinstance(coverage, dict):
        return str(coverage["base_commit"])
    if "..." in diff_range:
        left, right = diff_range.split("...", 1)
        return _git(repo, "merge-base", left, right).strip()
    if ".." in diff_range:
        return diff_range.split("..", 1)[0]
    return f"{diff_range}^"


def _existed_at(repo: Path, revision: str, path: str) -> bool:
    return subprocess.run(
        ["git", "cat-file", "-e", f"{revision}:{path}"],
        cwd=repo,
        capture_output=True,
    ).returncode == 0


def _validate_coverage_commits(
    coverage: dict[str, Any], repo: Path, right: str
) -> list[str]:
    base = coverage["base_commit"]
    for label, commit in (("coverage base", base), ("coverage range tip", right)):
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
            cwd=repo,
            capture_output=True,
        )
        if proc.returncode:
            raise ValueError(f"{label} is not a local commit: {commit}")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", base, right],
        cwd=repo,
        capture_output=True,
    ).returncode:
        raise ValueError("coverage.base_commit is not an ancestor of the range tip")
    commits = _git(repo, "rev-list", "--reverse", f"{base}..{right}").splitlines()
    in_range = set(commits)
    excluded = {item["commit"] for item in coverage.get("excluded_commits", [])}
    outside = excluded - in_range
    if outside:
        raise ValueError(
            "excluded commits are outside the coverage range: "
            + ", ".join(sorted(outside))
        )
    return [commit for commit in commits if commit not in excluded]


def _coverage_changes(
    data: dict[str, Any], repo: Path, diff_range: str
) -> tuple[list[tuple[str, list[str]]], list[str]] | None:
    coverage = data.get("coverage")
    if coverage is None:
        return None
    commits = _validate_coverage_commits(coverage, repo, _range_right(diff_range))
    changes: list[tuple[str, list[str]]] = []
    for commit in commits:
        changes.extend(_changed_paths(repo, f"{commit}^..{commit}"))
    return changes, commits


def validate_diff_coverage(data: dict[str, Any], repo: Path, diff_range: str) -> None:
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
    changes = scoped[0] if scoped is not None else _changed_paths(repo, diff_range)
    baseline = _range_base(data, repo, diff_range)
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
                diff_range,
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


def _changed_lines(repo: Path, diff_range: str, paths: list[str] | None = None) -> str:
    args = ["diff", "--unified=0", diff_range]
    if paths:
        args.extend(["--", *paths])
    text = _git(repo, *args)
    return "\n".join(
        line for line in text.splitlines()
        if (line.startswith("+") and not line.startswith("+++"))
        or (line.startswith("-") and not line.startswith("---"))
    )


def _symbol_spans(source: str, symbols: list[str]) -> dict[str, list[tuple[int, int]]]:
    wanted = {symbol.rsplit(".", 1)[-1]: symbol for symbol in symbols}
    spans = {symbol: [] for symbol in symbols}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return spans
    for node in ast.walk(tree):
        name = getattr(node, "name", None)
        if name in wanted and hasattr(node, "lineno"):
            spans[wanted[name]].append((node.lineno, getattr(node, "end_lineno", node.lineno)))
    return spans


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
    entry: dict[str, Any], repo: Path, diff_range: str, paths: list[str]
) -> list[str]:
    left = diff_range.split("..", 1)[0]
    hits: set[str] = set()
    for path in paths:
        diff = _git(repo, "diff", "--unified=0", diff_range, "--", path)
        old_changed, new_changed = _changed_line_numbers(diff)
        try:
            new_source = (repo / path).read_text(encoding="utf-8")
        except OSError:
            new_source = ""
        old_source = _git(repo, "show", f"{left}:{path}", check=False)
        for symbol, spans in _symbol_spans(new_source, entry["owned_symbols"]).items():
            if any(any(start <= line <= end for line in new_changed) for start, end in spans):
                hits.add(symbol)
        for symbol, spans in _symbol_spans(old_source, entry["owned_symbols"]).items():
            if any(any(start <= line <= end for line in old_changed) for start, end in spans):
                hits.add(symbol)
        changed_text = _changed_lines(repo, diff_range, [path])
        for symbol in entry["owned_symbols"]:
            if re.search(rf"\b{re.escape(symbol)}\b", changed_text):
                hits.add(symbol)
    return sorted(hits)


def classify_upstream_overlap(
    entry: dict[str, Any], repo: Path, diff_range: str
) -> dict[str, Any]:
    changes = _changed_paths(repo, diff_range)
    changed = {path for _status, paths in changes for path in paths}
    owned_files = set(entry["files"])
    same_files = sorted(changed & owned_files)
    symbols = entry["owned_symbols"]
    owned_hits = _owned_symbol_hits(entry, repo, diff_range, same_files)
    if owned_hits:
        classification = "owned_symbol"
        rationale = f"owned symbols changed: {', '.join(owned_hits)}"
    elif same_files:
        classification = "same_file"
        rationale = f"same ledger-owned file changed: {', '.join(same_files)}"
    else:
        all_lines = _changed_lines(repo, diff_range)
        equivalent_hits = sorted(
            symbol for symbol in symbols
            if re.search(rf"\b{re.escape(symbol)}\b", all_lines)
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
        "owned_symbols": entry["owned_symbols"],
        "expected_commit_subject": entry["expected_commit_subject"],
        "classification": classification,
        "rationale": rationale,
        "merge_guidance": entry["merge_guidance"],
        "removal_condition": entry["removal_condition"],
        "tests": entry["tests"],
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
    parser.add_argument("--manifest", type=Path, required=True)
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
        data = load_and_validate_manifest(args.manifest, repo)
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
            if any(
                item["classification"] in {"owned_symbol", "possible_upstream_equivalent"}
                and not item["acknowledged"]
                for item in overlaps
            ):
                return 2
    except ValueError as exc:
        print(f"customization ledger error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
