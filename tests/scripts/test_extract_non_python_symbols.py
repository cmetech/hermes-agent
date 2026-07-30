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


def _run_raw(
    *records: dict[str, object], timeout: float = 3,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    payload = "\n".join(json.dumps(item) for item in records) + "\n"
    result = subprocess.run(
        ["node", str(HELPER)], cwd=ROOT, input=payload,
        text=True, capture_output=True, check=False, timeout=timeout,
    )
    return result, [json.loads(line) for line in result.stdout.splitlines()]


def _run_helper(
    *requests: dict[str, object], timeout: float = 3,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    return _run_raw({"type": "hello", "protocol": 1}, *requests, timeout=timeout)


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


def test_markdown_parser_excludes_comments_embedded_in_raw_html() -> None:
    source = b"<div>\n<!-- ExactToken -->\n</div>"
    result, records = _run_helper(_request("wrapped.md", "markdown", source, ["ExactToken"]))
    assert result.returncode == 0, result.stderr
    assert _response(records, "wrapped.md")["spans"] == {"ExactToken": []}


def test_markdown_parser_uses_html_comment_grammar_not_raw_delimiters() -> None:
    source = b'''<div title="<!-- ExactToken -->">visible</div>

<div>
<!-- ExactToken -->
</div>

<!-- ExactToken'''
    result, records = _run_helper(
        _request("comment-grammar.md", "markdown", source, ["ExactToken"])
    )
    assert result.returncode == 0, result.stderr
    attribute_start = source.index(b"ExactToken")
    assert _response(records, "comment-grammar.md")["spans"] == {
        "ExactToken": [[attribute_start, attribute_start + len(b"ExactToken")]],
    }


def test_markdown_parser_excludes_unclosed_comments_in_commonmark_containers() -> None:
    for index, source in enumerate((
        b"<!-- ExactToken",
        b"> <!-- ExactToken",
        b"- <!-- ExactToken",
        b"> - <!-- ExactToken",
    )):
        request_id = f"container-{index}.md"
        result, records = _run_helper(
            _request(request_id, "markdown", source, ["ExactToken"])
        )
        assert result.returncode == 0, result.stderr
        assert _response(records, request_id)["spans"] == {"ExactToken": []}


def test_markdown_parser_keeps_comment_text_inside_container_html_attributes() -> None:
    source = b'''> <span title="<!-- ExactToken -->">visible</span>'''
    result, records = _run_helper(
        _request("container-attribute.md", "markdown", source, ["ExactToken"])
    )
    assert result.returncode == 0, result.stderr
    assert _response(records, "container-attribute.md")["spans"] == {
        "ExactToken": _expected_occurrences(source, b"ExactToken"),
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
        error_id = request["id"]
        assert records[-1] == {
            "type": "error",
            "id": error_id,
            "code": code,
            "detail": f"{code}: {error_id if error_id is not None else 'null'}",
        }
        assert secret not in result.stdout
        assert secret not in result.stderr


def test_helper_rejects_empty_requested_symbols_without_hanging() -> None:
    result, records = _run_helper(_request("empty.ts", "typescript", b"const value = 1;", [""]), timeout=1)
    assert result.returncode != 0
    assert records[-1] == {
        "type": "error",
        "id": "empty.ts",
        "code": "invalid_symbol",
        "detail": "invalid_symbol: empty.ts",
    }


def test_helper_bounds_oversized_error_metadata() -> None:
    oversized_id = "x" * 10_000
    request = {
        "type": "parse",
        "id": oversized_id,
        "path": "oversized.ts",
        "language": "typescript",
        "source_base64": base64.b64encode(b"const value = 1;").decode("ascii"),
    }
    result, records = _run_helper(request)
    assert result.returncode != 0
    error = records[-1]
    assert error == {
        "type": "error",
        "id": None,
        "code": "metadata_limit",
        "detail": "metadata_limit: null",
    }
    assert len(json.dumps(error, separators=(",", ":"))) <= 256
    assert oversized_id not in result.stdout
    assert oversized_id not in result.stderr


def test_helper_accepts_long_paths_with_an_opaque_request_id() -> None:
    path = ("nested/" * 500) + "owned.ts"
    assert len(path.encode("utf-8")) < 4096
    request = _request(path, "typescript", b"const ExactToken = 1;", ["ExactToken"])
    request["id"] = "request-00000000"

    result, records = _run_helper(request)

    assert result.returncode == 0, result.stderr
    assert _response(records, "request-00000000")["spans"] == {
        "ExactToken": [[6, 16]],
    }


def test_helper_enforces_aligned_id_path_and_symbol_metadata_boundaries() -> None:
    valid_path = ("p" * 4093) + ".ts"
    valid_symbol = "é" * 128
    valid = _request(
        valid_path,
        "typescript",
        f"const value = '{valid_symbol}';".encode(),
        [valid_symbol],
    )
    valid["id"] = "r" * 128
    result, records = _run_helper(valid)
    assert result.returncode == 0, result.stderr
    assert _response(records, "r" * 128)["spans"][valid_symbol]

    invalid_records = []
    oversized_id = dict(valid, id="r" * 129)
    invalid_records.append(oversized_id)
    oversized_path = dict(valid, id="path-limit", path=("p" * 4094) + ".ts")
    invalid_records.append(oversized_path)
    oversized_symbol = "é" * 129
    invalid_records.append(dict(
        valid,
        id="symbol-limit",
        symbols=[oversized_symbol],
        source_base64=base64.b64encode(
            f"const value = '{oversized_symbol}';".encode()
        ).decode("ascii"),
    ))
    for request in invalid_records:
        result, records = _run_helper(request)
        assert result.returncode != 0
        assert records[-1]["code"] == "metadata_limit"
        assert valid_symbol not in result.stdout
