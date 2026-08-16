#!/usr/bin/env python3
"""Structural validator for an implementation plan.

Every round of review so far found defects my string-grep checks passed over:
tests nested inside an implementation block after `return`, tests asserting on
a design the implementation no longer uses, mocks whose signature no longer
matches the call, counts that drifted. All of those are visible if you parse
the code blocks instead of grepping them.

Usage: validate_plan.py <plan.md>
"""
import ast
import re
import sys
from collections import defaultdict


def blocks(text):
    """Yield (lang, body, start_line) for every fenced block."""
    out, lang, buf, start = [], None, [], 0
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if lang is None:
                lang, buf, start = stripped[3:].strip() or "text", [], i
            else:
                out.append((lang, "\n".join(buf), start))
                lang = None
        elif lang is not None:
            buf.append(line)
    if lang is not None:
        out.append(("UNCLOSED", "\n".join(buf), start))
    return out


def task_of(text, line):
    """Which task does this line fall in?"""
    last = "(preamble)"
    for m in re.finditer(r"^### (Task \d+):", text, re.M):
        if text[: m.start()].count("\n") + 1 <= line:
            last = m.group(1)
        else:
            break
    return last


def main(path):
    text = open(path, encoding="utf-8").read()
    problems = []

    # 1. fences balanced
    fences = sum(1 for l in text.splitlines() if l.strip().startswith("```"))
    if fences % 2:
        problems.append(f"FENCES: {fences} markers - unbalanced")

    py = [(b, s) for lang, b, s in blocks(text) if lang == "python"]
    if any(lang == "UNCLOSED" for lang, _, _ in blocks(text)):
        problems.append("FENCES: an unclosed block reaches end of file")

    def parse(body):
        """Parse a block, tolerating fragments meant to be pasted into a class.

        Many blocks are method bodies or a run of methods with class-level
        indentation. Those are legitimate, so retry dedented and retry wrapped
        before calling it a syntax error.
        """
        import textwrap

        for candidate in (
            body,
            textwrap.dedent(body),
            "class _Frag:\n" + body,
            "class _Frag:\n" + textwrap.indent(textwrap.dedent(body), "    "),
        ):
            try:
                return ast.parse(candidate)
            except SyntaxError:
                continue
        return None

    # 2. every python block parses, as written or as a class fragment
    for body, start in py:
        if parse(body) is None:
            try:
                ast.parse(body)
            except SyntaxError as exc:
                problems.append(
                    f"SYNTAX: {task_of(text, start)} block at line {start}: "
                    f"{exc.msg} (block line {exc.lineno})"
                )

    # 3. no test function nested inside another function / unreachable
    for body, start in py:
        tree = parse(body)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name.startswith("test_"):
                    problems.append(
                        f"NESTED-TEST: {task_of(text, start)} line ~{start + child.lineno}: "
                        f"{child.name} is nested inside {node.name}() - it will never run"
                    )

    # 4. names used in tests that are never defined or imported anywhere
    defined = set()
    for body, _ in py:
        tree = parse(body)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                defined.add(node.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    defined.add((a.asname or a.name).split(".")[0])
            elif isinstance(node, ast.arg):
                defined.add(node.arg)
    BUILTINS = set(dir(__builtins__)) | {
        "self", "mock", "pytest", "os", "sys", "re", "json", "Path", "time",
        "threading", "hashlib", "stat", "subprocess", "config", "sk", "keyring",
        "inspect", "importlib",
    }
    for body, start in py:
        tree = parse(body)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                n = node.id
                if n.isupper() and n not in defined and n not in BUILTINS:
                    problems.append(
                        f"UNDEFINED: {task_of(text, start)} line ~{start + node.lineno}: "
                        f"constant {n} is used but never assigned in any block"
                    )

    # 5. stated PASS counts vs actual test defs, per test file, cumulative
    per_file = defaultdict(int)
    tasks = [(m.group(1), m.start()) for m in re.finditer(r"^### (Task \d+):", text, re.M)]
    tasks.append(("END", len(text)))
    for i, (name, s0) in enumerate(tasks[:-1]):
        body = text[s0 : tasks[i + 1][1]]
        files = set(re.findall(r"tests/[\w/]+/(test_\w+\.py)", body))
        n = len(re.findall(r"^\s+def (test_\w+)", body, re.M))
        stated = re.findall(r"Expected: PASS \((\d+) tests?\)", body)
        if not stated:
            continue
        if len(files) == 1:
            f = files.pop()
            per_file[f] += n
            if int(stated[-1]) != per_file[f]:
                problems.append(
                    f"COUNT: {name} states PASS ({stated[-1]}) but {f} "
                    f"cumulatively has {per_file[f]}"
                )

    # 6. symbols the plan says it implements vs symbols the tests reference
    impl = set(re.findall(r"^def (\w+)|^    def (\w+)", text, re.M))
    impl = {a or b for a, b in impl}
    for body, start in py:
        for m in re.finditer(r"_current_windows_\w+|probe_os_keystore|_demote_os_backend", body):
            sym = m.group(0)
            if sym not in text.split("def " + sym)[0] and f"def {sym}" not in text:
                problems.append(
                    f"MISSING-SYMBOL: {task_of(text, start)} references {sym}, "
                    f"which no block defines"
                )

    # 7. step numbering contiguous per task
    cur, steps = None, []
    for line in text.splitlines():
        m = re.match(r"^### (Task \d+):", line)
        if m:
            if cur and steps and steps != list(range(1, len(steps) + 1)):
                problems.append(f"STEPS: {cur} numbered {steps}")
            cur, steps = m.group(1), []
        m2 = re.match(r"^- \[ \] \*\*Step (\d+):", line)
        if m2 and cur:
            steps.append(int(m2.group(1)))
    if cur and steps and steps != list(range(1, len(steps) + 1)):
        problems.append(f"STEPS: {cur} numbered {steps}")

    # 8. developer home paths
    for m in re.finditer(r"/Users/|/home/[^/\s]+/", text):
        problems.append(f"HOME-PATH: line {text[:m.start()].count(chr(10))+1}")

    print(f"{len(py)} python blocks parsed")
    if problems:
        print(f"\n{len(problems)} PROBLEM(S):")
        for p in dict.fromkeys(problems):
            print(f"  - {p}")
        return 1
    print("\nno structural problems found")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
