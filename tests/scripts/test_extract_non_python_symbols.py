"""Protocol-level tests for the pinned non-Python symbol parser helper."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[2]
HELPER = ROOT / "scripts/extract_non_python_symbols.mjs"


def _request(path: str, language: str, source: bytes, symbols: list[str]) -> dict[str, object]:
    return {
        "type": "parse",
        "id": path,
        "path": path,
        "language": language,
        "source_base64": base64.b64encode(source).decode("ascii"),
        "symbols": symbols,
    }


def _run_raw(*records: dict[str, object]) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    payload = "\n".join(json.dumps(item) for item in records) + "\n"
    result = subprocess.run(
        ["node", str(HELPER)], cwd=ROOT, input=payload,
        text=True, capture_output=True, check=False,
    )
    return result, [json.loads(line) for line in result.stdout.splitlines()]


def _run_helper(*requests: dict[str, object]) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    return _run_raw({"type": "hello", "protocol": 1}, *requests)


def _expected_occurrences(source: bytes, token: bytes) -> list[list[int]]:
    found: list[list[int]] = []
    start = 0
    while (offset := source.find(token, start)) >= 0:
        found.append([offset, offset + len(token)])
        start = offset + 1
    return found


def _response(records: list[dict[str, object]], request_id: str) -> dict[str, object]:
    return next(record for record in records if record.get("type") == "result" and record.get("id") == request_id)


def _without_comment_matches(source: bytes, token: bytes, comments: list[bytes]) -> list[list[int]]:
    comment_starts = [source.index(comment) for comment in comments]
    excluded = {
        (comment_start + start, comment_start + end)
        for comment_start, comment in zip(comment_starts, comments)
        for start, end in _expected_occurrences(comment, token)
    }
    assert excluded
    return [span for span in _expected_occurrences(source, token) if tuple(span) not in excluded]


def test_helper_attests_exact_protocol_and_dependency_versions() -> None:
    result, records = _run_helper()
    assert result.returncode == 0, result.stderr
    assert records == [{
        "type": "hello",
        "protocol": 1,
        "versions": {
            "typescript": "6.0.3",
            "unified": "11.0.5",
            "remark-parse": "11.0.0",
            "micromark": "4.0.2",
        },
    }]


def test_typescript_parser_finds_broad_syntax_but_not_comments() -> None:
    source = b'''const obj = { ExactToken: 1 };
obj.ExactToken;
class Example { #ExactToken = 'ExactToken'; method() { return /ExactToken/.test(`ExactToken`); } }
const view = <ExactToken />;
// ExactToken
/* ExactToken */
'''
    result, records = _run_helper(_request("sample.tsx", "typescript", source, ["ExactToken"]))
    assert result.returncode == 0, result.stderr
    comments = [b"// ExactToken", b"/* ExactToken */"]
    assert _response(records, "sample.tsx")["spans"] == {
        "ExactToken": _without_comment_matches(source, b"ExactToken", comments),
    }


def test_typescript_parser_accepts_as_and_satisfies_contexts() -> None:
    source = b"const value = input as ExactToken; const checked = input satisfies ExactToken;"
    result, records = _run_helper(_request("contexts.ts", "typescript", source, ["ExactToken"]))
    assert result.returncode == 0, result.stderr
    assert _response(records, "contexts.ts")["spans"] == {
        "ExactToken": _expected_occurrences(source, b"ExactToken"),
    }


def test_typescript_parser_finds_qualified_relationships_without_owning_trivia() -> None:
    source = b"const first = Owner /* trivia */ . member; namespace Owner { export const member = 1; }"
    result, records = _run_helper(_request("qualified.ts", "typescript", source, ["Owner.member"]))
    assert result.returncode == 0, result.stderr
    first_owner = source.index(b"Owner")
    first_member = source.index(b"member")
    namespace_owner = source.index(b"Owner", first_owner + 1)
    namespace_member = source.index(b"member", first_member + 1)
    assert _response(records, "qualified.ts")["spans"] == {
        "Owner.member": [
            [first_owner, first_owner + len(b"Owner")],
            [first_member, first_member + len(b"member")],
            [namespace_owner, namespace_owner + len(b"Owner")],
            [namespace_member, namespace_member + len(b"member")],
        ],
    }


def test_markdown_parser_finds_broad_nodes_but_not_html_comments() -> None:
    source = b'''# ExactToken
[ExactToken](https://example.test/ExactToken)
`ExactToken`

```txt
ExactToken
```

> ExactToken
- ExactToken
<https://example.test/ExactToken>
<span>ExactToken</span>
<!-- ExactToken -->
'''
    result, records = _run_helper(_request("guide.md", "markdown", source, ["ExactToken"]))
    assert result.returncode == 0, result.stderr
    assert _response(records, "guide.md")["spans"] == {
        "ExactToken": _without_comment_matches(source, b"ExactToken", [b"<!-- ExactToken -->"]),
    }


def test_helper_returns_utf8_byte_offsets_for_multibyte_prefixes() -> None:
    source = "é🙂ExactToken".encode("utf-8")
    result, records = _run_helper(_request("unicode.md", "markdown", source, ["ExactToken"]))
    assert result.returncode == 0, result.stderr
    assert _response(records, "unicode.md")["spans"] == {
        "ExactToken": [[len("é🙂".encode("utf-8")), len("é🙂ExactToken".encode("utf-8"))]],
    }


def test_helper_rejects_invalid_base64_utf8_and_parse_diagnostics() -> None:
    cases = [
        (
            {"type": "parse", "id": "base64.ts", "path": "base64.ts", "language": "typescript", "source_base64": "%%%", "symbols": ["ExactToken"]},
            "invalid_base64",
            "%%%",
        ),
        (_request("utf8.ts", "typescript", b"\xffExactToken", ["ExactToken"]), "invalid_utf8", "ExactToken"),
        (_request("diagnostic.ts", "typescript", b"const ExactToken = ;", ["ExactToken"]), "parse_diagnostic", "const ExactToken = ;"),
    ]
    for request, code, secret in cases:
        result, records = _run_helper(request)
        assert result.returncode != 0
        assert len(records) == 2
        error_id = None if code == "parse_diagnostic" else request["id"]
        assert records[-1] == {
            "type": "error",
            "id": error_id,
            "code": code,
            "detail": f"{code}: {error_id if error_id is not None else 'null'}",
        }
        assert secret not in result.stdout
        assert secret not in result.stderr
