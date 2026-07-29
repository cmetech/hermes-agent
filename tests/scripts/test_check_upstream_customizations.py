"""Behavior contracts for the upstream-customization ledger checker."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import time

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
    # Keep the fixture's production and test sources internally consistent:
    # both declare or reference the symbol owned by the generated ledger.
    (repo / "test_core.py").write_text(
        "from core import Owned\n\n\ndef test_owned():\n    assert Owned\n"
    )
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


_EXPECTED_PARSER_VERSIONS = {
    "typescript": "6.0.3",
    "unified": "11.0.5",
    "remark-parse": "11.0.0",
    "micromark": "4.0.2",
}


def _parser_request(
    request_id: str = "request",
    *,
    source: bytes = b"const ExactToken = true;",
    symbols: tuple[str, ...] = ("ExactToken",),
    blob_oid: str = "1" * 40,
    path: str = "owned.ts",
) -> customization_checker._ParserRequest:
    return customization_checker._ParserRequest(
        request_id=request_id,
        path=path,
        language="typescript",
        blob_oid=blob_oid,
        source=source,
        symbols=symbols,
    )


def _parser_records(
    *results: dict[str, object],
    protocol: object = 1,
    versions: dict[str, str] | None = None,
) -> bytes:
    records: list[dict[str, object]] = [{
        "type": "hello",
        "protocol": protocol,
        "versions": versions or _EXPECTED_PARSER_VERSIONS,
    }, *results]
    return b"".join(
        json.dumps(record, separators=(",", ":")).encode("utf-8") + b"\n"
        for record in records
    )


def _parser_result(
    request_id: str = "request",
    spans: dict[str, list[list[int | bool]]] | None = None,
) -> dict[str, object]:
    return {
        "type": "result",
        "id": request_id,
        "spans": spans if spans is not None else {"ExactToken": [[6, 16]]},
    }


def _install_fake_parser_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: bytes,
    stderr: bytes = b"",
    returncode: int = 0,
) -> None:
    """Install a real short-lived executable at the process-launch boundary."""
    executable = tmp_path / "fake-node"
    encoded_stdout = base64.b64encode(stdout).decode("ascii")
    encoded_stderr = base64.b64encode(stderr).decode("ascii")
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import base64\n"
        "import sys\n"
        "sys.stdin.buffer.read()\n"
        f"sys.stdout.buffer.write(base64.b64decode({encoded_stdout!r}))\n"
        f"sys.stderr.buffer.write(base64.b64decode({encoded_stderr!r}))\n"
        f"raise SystemExit({returncode})\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    helper = tmp_path / "helper.mjs"
    helper.write_text("// controlled test fixture\n", encoding="utf-8")
    monkeypatch.setattr(customization_checker.shutil, "which", lambda _name: str(executable))
    monkeypatch.setattr(customization_checker, "_NON_PYTHON_HELPER", helper)


def _assert_bounded_parser_error(call) -> None:
    with pytest.raises(ValueError) as raised:
        call()
    message = str(raised.value)
    assert message.startswith("non-Python parser")
    assert "DO_NOT_ECHO_SECRET_SOURCE" not in message
    assert len(message) <= 512


def test_parser_blob_loader_reads_resolved_commit_not_dirty_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading the checkout instead of the resolved Git blob must fail this test."""
    repo = _repo(tmp_path)
    committed_source = b"export const ExactToken = true;\n"
    (repo / "owned.ts").write_bytes(committed_source)
    _git(repo, "add", "owned.ts")
    _git(repo, "commit", "-m", "add committed TypeScript")
    resolved_revision = _git(repo, "rev-parse", "HEAD")
    dirty_source = b"// ExactToken only exists in this dirty comment\n"
    (repo / "owned.ts").write_bytes(dirty_source)
    calls: list[list[customization_checker._ParserRequest]] = []

    def recording_batch(requests):
        calls.append(list(requests))
        return {
            request.request_id: {
                symbol: [
                    (offset, offset + len(symbol.encode("utf-8")))
                    for offset in [request.source.find(symbol.encode("utf-8"))]
                    if offset >= 0
                ]
                for symbol in request.symbols
            }
            for request in requests
        }

    monkeypatch.setattr(customization_checker, "_run_parser_batch", recording_batch)
    blob_oid, source = customization_checker._blob_bytes(
        repo, resolved_revision, "owned.ts"
    )
    request = _parser_request(
        source=source,
        blob_oid=blob_oid,
        request_id=f"{resolved_revision}:owned.ts",
    )

    customization_checker._NonPythonSymbolResolver().spans([request])

    committed_git_bytes = subprocess.check_output(
        ["git", "show", f"{resolved_revision}:owned.ts"], cwd=repo
    )
    assert calls[0][0].source == committed_git_bytes == committed_source
    assert calls[0][0].source != dirty_source


def test_parser_blob_loader_reads_requested_historical_revision_not_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolving HEAD instead of the requested historical commit must fail."""
    repo = _repo(tmp_path)
    (repo / "owned.ts").write_text("export const ExactToken = true;\n")
    _git(repo, "add", "owned.ts")
    _git(repo, "commit", "-m", "add historical token")
    older = _git(repo, "rev-parse", "HEAD")
    (repo / "owned.ts").write_text("export const Replacement = true;\n")
    _git(repo, "commit", "-am", "remove historical token")
    calls: list[list[customization_checker._ParserRequest]] = []

    def recording_batch(requests):
        calls.append(list(requests))
        return {
            request.request_id: {
                symbol: [
                    (offset, offset + len(symbol.encode("utf-8")))
                    for offset in [request.source.find(symbol.encode("utf-8"))]
                    if offset >= 0
                ]
                for symbol in request.symbols
            }
            for request in requests
        }

    monkeypatch.setattr(customization_checker, "_run_parser_batch", recording_batch)
    resolved_older = _git(repo, "rev-parse", older)
    blob_oid, source = customization_checker._blob_bytes(
        repo, resolved_older, "owned.ts"
    )
    request = _parser_request(
        source=source,
        blob_oid=blob_oid,
        request_id=f"{resolved_older}:owned.ts",
    )

    customization_checker._NonPythonSymbolResolver().spans([request])

    assert calls[0][0].blob_oid == _git(repo, "rev-parse", f"{older}:owned.ts")
    assert calls[0][0].source == b"export const ExactToken = true;\n"


def test_parser_blob_loader_rejects_oversized_metadata_before_content_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The committed payload must not be acquired after its size is known unsafe."""
    oid = "a" * 40
    calls: list[tuple[str, ...]] = []

    def metadata_only(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        if command[1:3] == ("rev-parse", "--verify"):
            return subprocess.CompletedProcess(
                argv, 0, stdout=f"{oid}\n".encode(), stderr=b""
            )
        if command[1:3] == ("cat-file", "-t"):
            return subprocess.CompletedProcess(argv, 0, stdout=b"blob\n", stderr=b"")
        if command[1:3] == ("cat-file", "-s"):
            size = (4 * 1024 * 1024) + 1
            return subprocess.CompletedProcess(
                argv, 0, stdout=f"{size}\n".encode(), stderr=b""
            )
        pytest.fail(f"committed content capture ran after oversized metadata: {command}")

    monkeypatch.setattr(customization_checker.subprocess, "run", metadata_only)

    with pytest.raises(ValueError) as raised:
        customization_checker._blob_bytes(tmp_path, "resolved", "nested/owned.ts")

    assert str(raised.value).startswith("non-Python parser input_limit")
    assert "nested/owned.ts" in str(raised.value)
    assert [command[1:3] for command in calls] == [
        ("rev-parse", "--verify"),
        ("cat-file", "-t"),
        ("cat-file", "-s"),
    ]


def test_blob_loader_preserves_oversized_non_parser_formats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oid = "b" * 40
    payload = b"x" * ((4 * 1024 * 1024) + 1)
    captures = 0

    def controlled_git(argv, **kwargs):
        nonlocal captures
        command = tuple(argv)
        if command[1:3] == ("rev-parse", "--verify"):
            return subprocess.CompletedProcess(
                argv, 0, stdout=f"{oid}\n".encode(), stderr=b""
            )
        if command[1:3] == ("cat-file", "-t"):
            return subprocess.CompletedProcess(argv, 0, stdout=b"blob\n", stderr=b"")
        if command[1:3] == ("cat-file", "-s"):
            return subprocess.CompletedProcess(
                argv, 0, stdout=f"{len(payload)}\n".encode(), stderr=b""
            )
        if command[1:3] == ("cat-file", "blob"):
            captures += 1
            return subprocess.CompletedProcess(argv, 0, stdout=payload, stderr=b"")
        pytest.fail(f"unexpected Git invocation: {command}")

    monkeypatch.setattr(customization_checker.subprocess, "run", controlled_git)

    assert customization_checker._blob_bytes(
        tmp_path, "resolved", "fixture.json"
    ) == (oid, payload)
    assert captures == 1


def test_blob_bytes_or_empty_returns_empty_for_exact_missing_tree_entry(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    revision = _git(repo, "rev-parse", "HEAD")

    assert customization_checker._blob_bytes_or_empty(
        repo, revision, "missing.ts"
    ) == (f"absent:{revision}:missing.ts", b"")


def test_blob_text_does_not_turn_git_tree_failure_into_empty_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def failing_git(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        if command[1:3] in {
            ("rev-parse", "--verify"),
            ("cat-file", "-e"),
            ("ls-tree", "-z"),
        }:
            return subprocess.CompletedProcess(argv, 128, stdout=b"", stderr=b"broken")
        pytest.fail(f"unexpected Git invocation: {command}")

    monkeypatch.setattr(customization_checker.subprocess, "run", failing_git)

    with pytest.raises(ValueError, match="cannot inspect"):
        customization_checker._blob_text(tmp_path, "resolved", "owned.ts")

    assert not any(command[1:3] == ("cat-file", "blob") for command in calls)


def test_blob_bytes_or_empty_does_not_turn_git_tree_failure_into_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def failing_git(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        if command[1:3] in {("cat-file", "-e"), ("ls-tree", "-z")}:
            return subprocess.CompletedProcess(argv, 128, stdout=b"", stderr=b"broken")
        pytest.fail(f"unexpected Git invocation: {command}")

    monkeypatch.setattr(customization_checker.subprocess, "run", failing_git)

    with pytest.raises(ValueError, match="cannot inspect"):
        customization_checker._blob_bytes_or_empty(
            tmp_path, "resolved", "owned.ts"
        )

    assert not any(command[1:3] == ("cat-file", "blob") for command in calls)


@pytest.mark.parametrize(
    ("scenario", "tree_type", "object_type", "object_size", "payload"),
    [
        ("non-blob", b"tree", b"tree\n", b"3\n", b"abc"),
        ("malformed-type", b"blob", b"blob extra\n", b"3\n", b"abc"),
        ("unreadable-object", b"blob", None, b"3\n", b"abc"),
        ("malformed-size", b"blob", b"blob\n", b"not-a-size\n", b"abc"),
        ("length-mismatch", b"blob", b"blob\n", b"4\n", b"abc"),
    ],
)
def test_blob_bytes_or_empty_fails_closed_for_present_invalid_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    tree_type: bytes,
    object_type: bytes | None,
    object_size: bytes,
    payload: bytes,
) -> None:
    oid = b"c" * 40
    calls: list[tuple[str, ...]] = []

    def controlled_git(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        if command[1:3] == ("cat-file", "-e"):
            return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")
        if command[1:3] == ("ls-tree", "-z"):
            entry = b"100644 " + tree_type + b" " + oid + b"\towned.ts\0"
            return subprocess.CompletedProcess(argv, 0, stdout=entry, stderr=b"")
        if command[1:3] == ("rev-parse", "--verify"):
            return subprocess.CompletedProcess(argv, 0, stdout=oid + b"\n", stderr=b"")
        if command[1:3] == ("cat-file", "-t"):
            return subprocess.CompletedProcess(
                argv,
                128 if object_type is None else 0,
                stdout=object_type or b"",
                stderr=b"missing" if object_type is None else b"",
            )
        if command[1:3] == ("cat-file", "-s"):
            return subprocess.CompletedProcess(argv, 0, stdout=object_size, stderr=b"")
        if command[1:3] == ("cat-file", "blob"):
            return subprocess.CompletedProcess(argv, 0, stdout=payload, stderr=b"")
        pytest.fail(f"unexpected Git invocation: {command}")

    monkeypatch.setattr(customization_checker.subprocess, "run", controlled_git)

    with pytest.raises(ValueError):
        customization_checker._blob_bytes_or_empty(
            tmp_path, "resolved", "owned.ts"
        )

    captured = [
        command for command in calls if command[1:3] == ("cat-file", "blob")
    ]
    assert bool(captured) is (scenario == "length-mismatch")


def test_parser_resolver_batches_paths_and_deduplicates_identical_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing the blob-keyed lifetime cache must make this test fail."""
    calls: list[list[customization_checker._ParserRequest]] = []

    def recording_batch(requests):
        calls.append(list(requests))
        return {
            request.request_id: {
                symbol: [
                    (offset, offset + len(symbol.encode("utf-8")))
                    for offset in [request.source.find(symbol.encode("utf-8"))]
                    if offset >= 0
                ]
                for symbol in request.symbols
            }
            for request in requests
        }

    monkeypatch.setattr(customization_checker, "_run_parser_batch", recording_batch)
    first = _parser_request("first", symbols=("Other", "ExactToken", "Other"))
    alias = _parser_request("alias", symbols=("ExactToken", "Other"))
    resolver = customization_checker._NonPythonSymbolResolver()

    results = resolver.spans([first, alias])
    again = resolver.spans([alias])

    assert [[request.request_id for request in batch] for batch in calls] == [["first"]]
    assert calls[0][0].symbols == ("ExactToken", "Other")
    assert results["first"] == results["alias"] == again["alias"]


def test_parser_batches_sequentially_at_sixteen_mibibytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent or over-limit batching rewrite must fail this test."""
    calls: list[list[customization_checker._ParserRequest]] = []

    def recording_batch(requests):
        calls.append(list(requests))
        return {
            request.request_id: {symbol: [] for symbol in request.symbols}
            for request in requests
        }

    monkeypatch.setattr(customization_checker, "_run_parser_batch", recording_batch)
    source = b"a" * ((4 * 1024 * 1024) - 1)
    requests = [
        _parser_request(
            f"request-{index}",
            source=source,
            blob_oid=f"{index + 1:040x}",
        )
        for index in range(5)
    ]

    results = customization_checker._NonPythonSymbolResolver().spans(requests)

    assert [len(batch) for batch in calls] == [4, 1]
    assert [request.request_id for batch in calls for request in batch] == list(results)
    assert all(
        sum(len(request.source) for request in batch) <= 16 * 1024 * 1024
        for batch in calls
    )


def test_parser_fails_closed_when_node_or_helper_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _parser_request()
    monkeypatch.setattr(customization_checker.shutil, "which", lambda _name: None)
    _assert_bounded_parser_error(lambda: customization_checker._run_parser_batch([request]))

    monkeypatch.undo()
    monkeypatch.setattr(
        customization_checker, "_NON_PYTHON_HELPER", tmp_path / "missing-helper.mjs"
    )
    _assert_bounded_parser_error(lambda: customization_checker._run_parser_batch([request]))


@pytest.mark.parametrize(
    ("protocol", "versions"),
    [
        (2, _EXPECTED_PARSER_VERSIONS),
        (1, {**_EXPECTED_PARSER_VERSIONS, "typescript": "0.0.0"}),
    ],
)
def test_parser_rejects_wrong_protocol_or_dependency_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    protocol: int,
    versions: dict[str, str],
) -> None:
    _install_fake_parser_process(
        tmp_path,
        monkeypatch,
        stdout=_parser_records(
            _parser_result(), protocol=protocol, versions=versions
        ),
    )

    _assert_bounded_parser_error(
        lambda: customization_checker._run_parser_batch([_parser_request()])
    )


@pytest.mark.parametrize("protocol", [True, 1.0], ids=("boolean", "float"))
def test_parser_rejects_non_integer_protocol_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    protocol: object,
) -> None:
    """Replacing exact integer typing with equality must fail this test."""
    _install_fake_parser_process(
        tmp_path,
        monkeypatch,
        stdout=_parser_records(_parser_result(), protocol=protocol),
    )

    _assert_bounded_parser_error(
        lambda: customization_checker._run_parser_batch([_parser_request()])
    )


@pytest.mark.parametrize(
    "separator",
    [b"\r", b"\v", b"\f"],
    ids=("bare-cr", "vertical-tab", "form-feed"),
)
def test_parser_rejects_non_lf_ndjson_framing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    separator: bytes,
) -> None:
    """Restoring broad splitlines framing must fail this test."""
    hello = json.dumps(
        {
            "type": "hello",
            "protocol": 1,
            "versions": _EXPECTED_PARSER_VERSIONS,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    result = json.dumps(_parser_result(), separators=(",", ":")).encode("utf-8")
    _install_fake_parser_process(
        tmp_path,
        monkeypatch,
        stdout=hello + separator + result + b"\n",
    )

    _assert_bounded_parser_error(
        lambda: customization_checker._run_parser_batch([_parser_request()])
    )


def test_parser_accepts_crlf_ndjson_framing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_parser_process(
        tmp_path,
        monkeypatch,
        stdout=_parser_records(_parser_result()).replace(b"\n", b"\r\n"),
    )

    assert customization_checker._run_parser_batch([_parser_request()]) == {
        "request": {"ExactToken": [(6, 16)]}
    }


@pytest.mark.parametrize(
    "source",
    [
        b"\xffDO_NOT_ECHO_SECRET_SOURCE",
        b"prefix\0DO_NOT_ECHO_SECRET_SOURCE",
        b"a" * ((4 * 1024 * 1024) + 1),
    ],
    ids=("invalid-utf8", "nul", "oversized"),
)
def test_parser_rejects_invalid_utf8_and_oversized_blob(
    monkeypatch: pytest.MonkeyPatch,
    source: bytes,
) -> None:
    monkeypatch.setattr(
        customization_checker,
        "_run_parser_batch",
        lambda _requests: pytest.fail("invalid source reached the parser process"),
    )

    _assert_bounded_parser_error(
        lambda: customization_checker._NonPythonSymbolResolver().spans(
            [_parser_request(source=source)]
        )
    )


def test_parser_kills_a_batch_after_sixty_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances = []

    class Sink:
        def write(self, data: bytes) -> int:
            return len(data)

        def close(self) -> None:
            return None

    class TimedOutProcess:
        def __init__(self, *args, **kwargs) -> None:
            self.stdin = Sink()
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()
            self.returncode = -9
            self.killed = False
            self.wait_timeouts: list[float | None] = []
            instances.append(self)

        def wait(self, timeout=None) -> int:
            self.wait_timeouts.append(timeout)
            if not self.killed:
                raise subprocess.TimeoutExpired("DO_NOT_ECHO_SECRET_SOURCE", timeout)
            return self.returncode

        def kill(self) -> None:
            self.killed = True

    monkeypatch.setattr(customization_checker.subprocess, "Popen", TimedOutProcess)

    _assert_bounded_parser_error(
        lambda: customization_checker._run_parser_batch([_parser_request()])
    )

    assert instances[0].killed is True
    assert instances[0].wait_timeouts[0] is not None
    assert 0 < instances[0].wait_timeouts[0] < 60
    assert instances[0].wait_timeouts[1] is not None
    assert 0 < instances[0].wait_timeouts[1] <= 1
    assert sum(instances[0].wait_timeouts) <= 60


def test_parser_batch_deadline_kills_pipe_retaining_descendants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unbounded drain-thread joins after a leader exits must fail this test."""
    executable = tmp_path / "pipe-retaining-node"
    response = base64.b64encode(_parser_records(_parser_result())).decode("ascii")
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import base64\n"
        "import subprocess\n"
        "import sys\n"
        "sys.stdin.buffer.read()\n"
        f"sys.stdout.buffer.write(base64.b64decode({response!r}))\n"
        "sys.stdout.buffer.flush()\n"
        "subprocess.Popen(\n"
        "    [sys.executable, '-c', 'import time; time.sleep(2)'],\n"
        "    stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr,\n"
        ")\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    helper = tmp_path / "helper.mjs"
    helper.write_text("// controlled test fixture\n", encoding="utf-8")
    monkeypatch.setattr(
        customization_checker.shutil, "which", lambda _name: str(executable)
    )
    monkeypatch.setattr(customization_checker, "_NON_PYTHON_HELPER", helper)
    monkeypatch.setattr(customization_checker, "_PARSER_TIMEOUT_SECONDS", 0.2)

    started = time.monotonic()
    _assert_bounded_parser_error(
        lambda: customization_checker._run_parser_batch([_parser_request()])
    )
    elapsed = time.monotonic() - started

    assert elapsed < 1


def test_parser_windows_job_composes_kill_on_close_lifecycle() -> None:
    """Omitting any Win32 containment operation must fail this test."""
    calls: list[tuple[object, ...]] = []

    class FakeKernel32:
        def CreateJobObjectW(self, security, name):
            calls.append(("create", security, name))
            return 73

        def SetInformationJobObject(self, handle, info_class, info, size):
            calls.append((
                "configure",
                handle,
                info_class,
                info._obj.BasicLimitInformation.LimitFlags,
                size,
            ))
            return 1

        def AssignProcessToJobObject(self, handle, process_handle):
            calls.append(("assign", handle, process_handle))
            return 1

        def TerminateJobObject(self, handle, exit_code):
            calls.append(("terminate", handle, exit_code))
            return 1

        def CloseHandle(self, handle):
            calls.append(("close", handle))
            return 1

    containment = customization_checker._WindowsJobContainment(FakeKernel32())
    containment.assign(91)
    containment.terminate()
    containment.close()

    assert [call[0] for call in calls] == [
        "create",
        "configure",
        "assign",
        "terminate",
        "close",
    ]
    assert calls[1][1:4] == (73, 9, 0x00002000)
    assert calls[2] == ("assign", 73, 91)


def test_parser_windows_job_assignment_precedes_payload_and_closes_containment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Starting the bootstrap before Job assignment must fail this test."""
    events: list[tuple[object, ...]] = []

    class FakeJob:
        def __init__(self) -> None:
            events.append(("job_created",))

        def assign(self, process_handle: int) -> None:
            events.append(("assigned", process_handle))

        def terminate(self) -> None:
            events.append(("terminated",))

        def close(self) -> None:
            events.append(("job_closed",))

    class Sink:
        def write(self, data: bytes) -> int:
            events.append(("payload", data))
            return len(data)

        def close(self) -> None:
            events.append(("stdin_closed",))

    class FakeProcess:
        pid = 77
        _handle = 88

        def __init__(self, argv, **kwargs) -> None:
            events.append(("process_started", tuple(argv), kwargs))
            self.stdin = Sink()
            self.stdout = io.BytesIO(b"bounded stdout")
            self.stderr = io.BytesIO()
            self.returncode = 0

        def wait(self, timeout=None) -> int:
            events.append(("waited", timeout))
            return self.returncode

        def kill(self) -> None:
            events.append(("leader_killed",))

    monkeypatch.setattr(
        customization_checker, "_is_windows", lambda: True, raising=False
    )
    monkeypatch.setattr(
        customization_checker,
        "_WindowsJobContainment",
        FakeJob,
        raising=False,
    )
    monkeypatch.setattr(customization_checker.subprocess, "Popen", FakeProcess)

    capture = customization_checker._run_bounded_capture(
        ["node", "helper.mjs"],
        cwd=Path.cwd(),
        input_bytes=b"parser payload",
        timeout_seconds=60,
        output_limit_bytes=1024,
    )

    event_names = [event[0] for event in events]
    assert event_names.index("job_created") < event_names.index("process_started")
    assert event_names.index("process_started") < event_names.index("assigned")
    assert event_names.index("assigned") < event_names.index("payload")
    assert ("payload", b"1parser payload") in events
    assert events[-1] == ("job_closed",)
    assert capture == customization_checker._CompletedCapture(
        returncode=0,
        stdout=b"bounded stdout",
        stderr=b"",
    )


@pytest.mark.parametrize("failure_stage", ["creation", "assignment"])
def test_parser_windows_containment_setup_failure_is_terminal_and_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    """Continuing after Job setup failure must fail this test."""
    secret = "DO_NOT_ECHO_SECRET_SOURCE"
    events: list[str] = []

    class FailingJob:
        def __init__(self) -> None:
            events.append("job_created")
            if failure_stage == "creation":
                raise OSError(secret)

        def assign(self, _process_handle: int) -> None:
            events.append("assign_attempted")
            raise OSError(secret)

        def terminate(self) -> None:
            events.append("job_terminated")

        def close(self) -> None:
            events.append("job_closed")

    class Sink:
        def write(self, data: bytes) -> int:
            return len(data)

        def close(self) -> None:
            return None

    class FakeProcess:
        pid = 77
        _handle = 88

        def __init__(self, *args, **kwargs) -> None:
            events.append("process_started")
            self.stdin = Sink()
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()
            self.returncode = -9

        def wait(self, timeout=None) -> int:
            events.append("leader_reaped")
            return self.returncode

        def kill(self) -> None:
            events.append("leader_killed")

    helper = tmp_path / "helper.mjs"
    helper.write_text("// controlled test fixture\n", encoding="utf-8")
    monkeypatch.setattr(
        customization_checker, "_is_windows", lambda: True, raising=False
    )
    monkeypatch.setattr(
        customization_checker, "_WindowsJobContainment", FailingJob, raising=False
    )
    monkeypatch.setattr(customization_checker.shutil, "which", lambda _name: "node")
    monkeypatch.setattr(customization_checker, "_NON_PYTHON_HELPER", helper)
    monkeypatch.setattr(customization_checker.subprocess, "Popen", FakeProcess)

    _assert_bounded_parser_error(
        lambda: customization_checker._run_parser_batch([_parser_request()])
    )

    if failure_stage == "creation":
        assert events == ["job_created"]
    else:
        assert events == [
            "job_created",
            "process_started",
            "assign_attempted",
            "leader_killed",
            "leader_reaped",
            "job_closed",
        ]


@pytest.mark.parametrize(
    "stdout",
    [
        b"x" * 65,
        b"not-json\n",
    ],
)
def test_parser_rejects_oversized_or_malformed_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: bytes,
) -> None:
    _install_fake_parser_process(tmp_path, monkeypatch, stdout=stdout)
    if len(stdout) > 64:
        monkeypatch.setattr(customization_checker, "_MAX_PARSER_OUTPUT_BYTES", 64)

    _assert_bounded_parser_error(
        lambda: customization_checker._run_parser_batch([_parser_request()])
    )


_RESPONSE_SHAPE_CASES = [
    "missing_id",
    "duplicate_id",
    "extra_id",
    "missing_symbol",
    "negative_start",
    "reversed_span",
    "endpoint_beyond_source",
    "duplicate_span",
    "unsorted_span",
    "overlapping_spans",
    "boolean_endpoint",
    "continuation_byte_endpoint",
]


@pytest.mark.parametrize("case", _RESPONSE_SHAPE_CASES)
def test_parser_rejects_unknown_missing_duplicate_and_invalid_spans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    source = "éExactToken zz ExactToken Other".encode("utf-8")
    exact_first = source.find(b"ExactToken")
    exact_second = source.find(b"ExactToken", exact_first + 1)
    other = source.find(b"Other")
    valid = {
        "ExactToken": [[exact_first, exact_first + 10]],
        "Other": [[other, other + 5]],
    }
    requests = [_parser_request(source=source, symbols=("ExactToken", "Other"))]
    results = [_parser_result(spans=valid)]
    if case == "missing_id":
        results = []
    elif case == "duplicate_id":
        requests.append(
            _parser_request(
                "second",
                source=source,
                symbols=("ExactToken", "Other"),
                blob_oid="2" * 40,
            )
        )
        results.append(_parser_result(spans=valid))
    elif case == "extra_id":
        requests.append(
            _parser_request(
                "second",
                source=source,
                symbols=("ExactToken", "Other"),
                blob_oid="2" * 40,
            )
        )
        results.append(_parser_result("unknown", valid))
    elif case == "missing_symbol":
        results[0] = _parser_result(spans={"ExactToken": valid["ExactToken"]})
    elif case == "negative_start":
        valid["ExactToken"] = [[-1, exact_first + 10]]
    elif case == "reversed_span":
        valid["ExactToken"] = [[exact_first + 10, exact_first]]
    elif case == "endpoint_beyond_source":
        valid["ExactToken"] = [[exact_first, len(source) + 1]]
    elif case == "duplicate_span":
        valid["ExactToken"] = [
            [exact_first, exact_first + 10],
            [exact_first, exact_first + 10],
        ]
    elif case == "unsorted_span":
        valid["ExactToken"] = [
            [exact_second, exact_second + 10],
            [exact_first, exact_first + 10],
        ]
    elif case == "overlapping_spans":
        valid["ExactToken"] = [
            [exact_first, exact_first + 10],
            [exact_first + 2, exact_first + 12],
        ]
    elif case == "boolean_endpoint":
        valid["ExactToken"] = [[True, exact_first + 10]]
    elif case == "continuation_byte_endpoint":
        valid["ExactToken"] = [[1, exact_first + 10]]
    _install_fake_parser_process(
        tmp_path, monkeypatch, stdout=_parser_records(*results)
    )

    _assert_bounded_parser_error(
        lambda: customization_checker._run_parser_batch(requests)
    )


def test_parser_errors_are_bounded_and_do_not_echo_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = b"DO_NOT_ECHO_SECRET_SOURCE"
    echoed = secret * 1_000
    _install_fake_parser_process(
        tmp_path,
        monkeypatch,
        stdout=_parser_records({
            "type": "error",
            "id": "request",
            "code": "internal_error",
            "detail": echoed.decode("ascii"),
        }),
        stderr=echoed,
        returncode=1,
    )

    _assert_bounded_parser_error(
        lambda: customization_checker._run_parser_batch(
            [_parser_request(source=secret)]
        )
    )


def test_parser_originated_request_failure_names_repository_path_without_source() -> None:
    secret = b"const DO_NOT_ECHO_SECRET_SOURCE = ;"
    request = _parser_request(
        "request-00000000",
        source=secret,
        path="nested/parser-owned.ts",
    )

    with pytest.raises(ValueError) as raised:
        customization_checker._run_parser_batch([request])

    message = str(raised.value)
    assert message.startswith("non-Python parser parse_diagnostic")
    assert "nested/parser-owned.ts" in message
    assert "request-00000000" not in message
    assert "DO_NOT_ECHO_SECRET_SOURCE" not in message
    assert len(message) <= 512


def test_parser_transport_accepts_valid_long_repository_path() -> None:
    path = ("nested/" * 500) + "owned.ts"
    request = _parser_request("request-00000000", path=path)

    assert customization_checker._run_parser_batch([request]) == {
        "request-00000000": {"ExactToken": [(6, 16)]}
    }


@pytest.mark.parametrize(
    "parser_request",
    [
        _parser_request("r" * 129),
        _parser_request("path-limit", path=("p" * 4094) + ".ts"),
        _parser_request("symbol-limit", symbols=("é" * 129,)),
    ],
    ids=("request-id", "path", "symbol"),
)
def test_parser_transport_rejects_metadata_beyond_aligned_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    parser_request: customization_checker._ParserRequest,
) -> None:
    monkeypatch.setattr(
        customization_checker,
        "_run_bounded_capture",
        lambda *args, **kwargs: pytest.fail("invalid metadata reached parser capture"),
    )

    _assert_bounded_parser_error(
        lambda: customization_checker._run_parser_batch([parser_request])
    )


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


def test_overlap_classification_distinguishes_file_symbol_and_unrelated_path(
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
    _git(repo, "commit", "-m", "unrelated matching public name")
    result = classify_upstream_overlap(entry, repo, f"{third}..HEAD")
    assert result["classification"] == "none"
    assert result["decision_required"] is False


def test_typescript_broad_symbols_include_as_satisfies_jsx_and_qualified_names(
    tmp_path: Path,
) -> None:
    """Qualified names split by trivia must retain their semantic relationship."""
    source = (
        "const cast = input as AsToken;\n"
        "const checked = input satisfies SatisfiesToken;\n"
        "const view = <JsxToken />;\n"
        "const qualified = Owner /* parser trivia */ . member;\n"
    )
    repo, source_path, manifest = _non_python_manifest(tmp_path, ".tsx", source)
    raw = yaml.safe_load(manifest.read_text())
    raw["upstream_changes"][0]["owned_symbols"] = [
        "AsToken",
        "SatisfiesToken",
        "JsxToken",
        "Owner.member",
    ]
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))
    baseline = _git(repo, "rev-parse", "HEAD")
    entry = load_and_validate_manifest(manifest, repo, check_git=False)[
        "upstream_changes"
    ][0]

    source_path.write_text(source.replace(". member", ". changedMember"))
    _git(repo, "commit", "-am", "edit qualified member")

    assert (
        classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")[
            "classification"
        ]
        == "owned_symbol"
    )


def test_markdown_broad_symbols_include_links_code_containers_and_html(
    tmp_path: Path,
) -> None:
    """Narrow Markdown leaf selection must still cover every public syntax form."""
    source = """# HeadingToken
[LinkToken](https://example.test/DestinationToken)
`InlineCodeToken`

```txt
FencedToken
```

> - ContainerToken
<span>HtmlToken</span>
<!-- CommentOnlyToken -->
"""
    repo, source_path, manifest = _non_python_manifest(tmp_path, ".md", source)
    raw = yaml.safe_load(manifest.read_text())
    raw["upstream_changes"][0]["owned_symbols"] = [
        "HeadingToken",
        "LinkToken",
        "DestinationToken",
        "InlineCodeToken",
        "FencedToken",
        "ContainerToken",
        "HtmlToken",
    ]
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))
    baseline = _git(repo, "rev-parse", "HEAD")
    entry = load_and_validate_manifest(manifest, repo, check_git=False)[
        "upstream_changes"
    ][0]

    source_path.write_text(
        source.replace("DestinationToken", "DestinationT0ken")
    )
    _git(repo, "commit", "-am", "edit markdown link destination")

    assert (
        classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")[
            "classification"
        ]
        == "owned_symbol"
    )


def test_markdown_html_attribute_comment_text_is_searchable_end_to_end(
    tmp_path: Path,
) -> None:
    source = '<div title="<!-- ExactToken -->">visible</div>\n'
    repo, source_path, manifest = _non_python_manifest(tmp_path, ".md", source)
    entry = load_and_validate_manifest(manifest, repo, check_git=False)[
        "upstream_changes"
    ][0]
    left = _git(repo, "rev-parse", "HEAD")

    source_path.write_text(source.replace("ExactToken", "ExactT0ken"))
    _git(repo, "commit", "-am", "edit non-comment html attribute")

    assert (
        classify_upstream_overlap(entry, repo, f"{left}..HEAD")["classification"]
        == "owned_symbol"
    )


def test_markdown_unclosed_html_comment_is_excluded_end_to_end(
    tmp_path: Path,
) -> None:
    source = "ExactToken\n\n<!-- ExactToken\n"
    repo, source_path, manifest = _non_python_manifest(tmp_path, ".md", source)
    entry = load_and_validate_manifest(manifest, repo, check_git=False)[
        "upstream_changes"
    ][0]
    left = _git(repo, "rev-parse", "HEAD")

    source_path.write_text(source.replace("<!-- ExactToken", "<!-- ExactT0ken"))
    _git(repo, "commit", "-am", "edit unclosed html comment")

    assert (
        classify_upstream_overlap(entry, repo, f"{left}..HEAD")["classification"]
        == "same_file"
    )

    source_path.write_text("<!-- ExactToken\n")
    _git(repo, "commit", "-am", "leave only unclosed html comment")
    with pytest.raises(ValueError, match="does not exist in declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_non_python_overlap_uses_utf8_byte_ranges_after_multibyte_prefix(
    tmp_path: Path,
) -> None:
    """Character offsets after UTF-8 prefixes must not drift onto owned bytes."""
    repo, source_path, manifest = _non_python_manifest(
        tmp_path, ".ts", 'const café = "ExactToken";\n'
    )
    entry = load_and_validate_manifest(manifest, repo, check_git=False)[
        "upstream_changes"
    ][0]
    left = _git(repo, "rev-parse", "HEAD")

    source_path.write_text('const café = "ExactT0ken";\n', encoding="utf-8")
    _git(repo, "add", source_path.name)
    _git(repo, "commit", "-m", "edit owned literal")
    assert (
        classify_upstream_overlap(entry, repo, f"{left}..HEAD")["classification"]
        == "owned_symbol"
    )

    _git(repo, "checkout", "-b", "unicode-sibling", left)
    source_path.write_text('const cafe = "ExactToken";\n', encoding="utf-8")
    _git(repo, "commit", "-am", "edit only multibyte identifier")
    assert (
        classify_upstream_overlap(entry, repo, f"{left}..HEAD")["classification"]
        == "same_file"
    )


@pytest.mark.parametrize(
    ("suffix", "before", "after"),
    [
        (
            ".ts",
            '// ExactToken before\nconst value = "ExactToken";\n',
            '// ExactToken after\nconst value = "ExactToken";\n',
        ),
        (
            ".md",
            '<!-- ExactToken before -->\nExactToken\n',
            '<!-- ExactToken after -->\nExactToken\n',
        ),
    ],
)
def test_non_python_overlap_ignores_comment_only_edits(
    tmp_path: Path,
    suffix: str,
    before: str,
    after: str,
) -> None:
    """A changed parser-excluded comment cannot borrow a live token elsewhere."""
    repo, source_path, manifest = _non_python_manifest(tmp_path, suffix, before)
    baseline = _git(repo, "rev-parse", "HEAD")
    entry = load_and_validate_manifest(manifest, repo, check_git=False)[
        "upstream_changes"
    ][0]
    source_path.write_text(after)
    _git(repo, "commit", "-am", "edit only parser-excluded comment")

    assert (
        classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")[
            "classification"
        ]
        == "same_file"
    )


def test_classify_all_entries_uses_one_shared_parser_resolution_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-entry parser calls would multiply work and break report-wide sharing."""
    repo = _repo(tmp_path)
    first = repo / "first.js"
    second = repo / "second.md"
    equivalent = repo / "equivalent.js"
    first.write_text("const FirstToken = 1;\n")
    second.write_text("SecondToken\n")
    equivalent.write_text("const unrelated = 1;\n")
    _git(repo, "add", first.name, second.name, equivalent.name)
    _git(repo, "commit", "-m", "add shared parser fixtures")
    left = _git(repo, "rev-parse", "HEAD")
    first.write_text("const FirstT0ken = 1;\n")
    second.write_text("SecondToken\nextra\n")
    equivalent.write_text("const SecondToken = 1;\n")
    _git(repo, "commit", "-am", "change three parser blobs")
    right = _git(repo, "rev-parse", "HEAD")

    template = yaml.safe_load(_manifest(repo, left).read_text())["upstream_changes"][0]
    entries = [
        {
            **template,
            "id": "first",
            "files": [first.name],
            "owned_symbols": ["FirstToken"],
        },
        {
            **template,
            "id": "second",
            "files": [second.name],
            "owned_symbols": ["SecondToken"],
        },
    ]
    calls: list[list[customization_checker._ParserRequest]] = []

    def parse(
        requests: list[customization_checker._ParserRequest],
    ) -> dict[str, dict[str, list[tuple[int, int]]]]:
        calls.append(list(requests))
        results: dict[str, dict[str, list[tuple[int, int]]]] = {}
        for request in requests:
            symbol_results: dict[str, list[tuple[int, int]]] = {}
            for symbol in request.symbols:
                token = symbol.encode("utf-8")
                spans: list[tuple[int, int]] = []
                cursor = 0
                while (start := request.source.find(token, cursor)) >= 0:
                    spans.append((start, start + len(token)))
                    cursor = start + 1
                symbol_results[symbol] = spans
            results[request.request_id] = symbol_results
        return results

    monkeypatch.setattr(customization_checker, "_run_parser_batch", parse)

    overlaps = customization_checker.classify_upstream_overlaps(
        entries, repo, f"{left}..{right}"
    )

    assert len(calls) == 1
    assert [(request.path, request.symbols) for request in calls[0]] == [
        ("first.js", ("FirstToken",)),
        ("first.js", ("FirstToken",)),
        ("second.md", ("SecondToken",)),
        ("second.md", ("SecondToken",)),
    ]
    assert [request.request_id for request in calls[0]] == [
        "request-00000000",
        "request-00000001",
        "request-00000002",
        "request-00000003",
    ]
    assert [item["id"] for item in overlaps] == ["first", "second"]
    assert [item["classification"] for item in overlaps] == [
        "owned_symbol",
        "same_file",
    ]


def test_overlap_parser_requests_ignore_many_unrelated_upstream_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unrelated parser files must not consume overlap-parser capacity."""
    repo = _repo(tmp_path)
    owned = repo / "owned.ts"
    owned.write_text("const OwnedToken = false;\n")
    unrelated = [repo / f"unrelated-{index}.ts" for index in range(64)]
    for path in unrelated:
        path.write_text("const OwnedToken = false;\n")
    _git(repo, "add", owned.name, *(path.name for path in unrelated))
    _git(repo, "commit", "-m", "add overlap parser fixtures")
    left = _git(repo, "rev-parse", "HEAD")
    owned.write_text("const OwnedT0ken = false;\n")
    for path in unrelated:
        path.write_text("const OwnedT0ken = false;\n")
    _git(repo, "commit", "-am", "change owned and unrelated parser files")

    template = yaml.safe_load(_manifest(repo, left).read_text())["upstream_changes"][0]
    entry = {
        **template,
        "files": [owned.name],
        "owned_symbols": ["OwnedToken"],
    }
    calls: list[list[customization_checker._ParserRequest]] = []

    def recording_batch(
        requests: list[customization_checker._ParserRequest],
    ) -> dict[str, dict[str, list[tuple[int, int]]]]:
        calls.append(list(requests))
        return {
            request.request_id: {
                symbol: [
                    (offset, offset + len(symbol.encode("utf-8")))
                    for offset in [request.source.find(symbol.encode("utf-8"))]
                    if offset >= 0
                ]
                for symbol in request.symbols
            }
            for request in requests
        }

    monkeypatch.setattr(customization_checker, "_run_parser_batch", recording_batch)

    result = classify_upstream_overlap(entry, repo, f"{left}..HEAD")

    assert result["classification"] == "owned_symbol"
    assert len(calls) == 1
    assert [(request.path, request.symbols) for request in calls[0]] == [
        ("owned.ts", ("OwnedToken",)),
        ("owned.ts", ("OwnedToken",)),
    ]
    assert not any(request.path.startswith("unrelated-") for request in calls[0])


def test_overlap_checks_upstream_addition_at_declared_custom_path(
    tmp_path: Path,
) -> None:
    """A ledger path added by upstream is still a candidate overlap."""
    repo = _repo(tmp_path)
    left = _git(repo, "rev-parse", "HEAD")
    template = yaml.safe_load(_manifest(repo, left).read_text())["upstream_changes"][0]
    entry = {
        **template,
        "files": ["custom.ts"],
        "owned_symbols": ["OwnedToken"],
    }
    (repo / "custom.ts").write_text("const OwnedToken = true;\n")
    _git(repo, "add", "custom.ts")
    _git(repo, "commit", "-m", "add declared custom path")

    result = classify_upstream_overlap(entry, repo, f"{left}..HEAD")

    assert result["classification"] == "owned_symbol"
    assert result["decision_required"] is True


def test_manifest_uses_one_shared_parser_resolution_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolving entries independently would duplicate blobs and parser batches."""
    repo = _repo(tmp_path)
    javascript = repo / "shared.js"
    markdown = repo / "guide.md"
    javascript.write_text("const FirstToken = SecondToken;\n")
    markdown.write_text("MarkdownToken\n")
    _git(repo, "add", javascript.name, markdown.name)
    _git(repo, "commit", "-m", "add manifest parser fixtures")
    revision = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, revision)
    template = yaml.safe_load(manifest.read_text())["upstream_changes"][0]
    raw = {
        "schema_version": 1,
        "upstream_changes": [
            {
                **template,
                "id": "first",
                "files": [javascript.name, markdown.name],
                "owned_symbols": ["FirstToken", "MarkdownToken"],
            },
            {
                **template,
                "id": "second",
                "files": [javascript.name],
                "owned_symbols": ["SecondToken"],
            },
        ],
    }
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))
    calls: list[list[customization_checker._ParserRequest]] = []

    def parse(requests):
        calls.append(list(requests))
        return {
            request.request_id: {
                symbol: [
                    (start, start + len(symbol.encode("utf-8")))
                    for start in range(len(request.source))
                    if request.source.startswith(symbol.encode("utf-8"), start)
                ]
                for symbol in request.symbols
            }
            for request in requests
        }

    monkeypatch.setattr(customization_checker, "_run_parser_batch", parse)

    data = load_and_validate_manifest(manifest, repo, check_git=False)

    assert [entry["id"] for entry in data["upstream_changes"]] == [
        "first",
        "second",
    ]
    assert len(calls) == 1
    assert [request.request_id for request in calls[0]] == [
        "request-00000000",
        "request-00000001",
    ]
    assert [request.symbols for request in calls[0]] == [
        ("FirstToken", "MarkdownToken"),
        ("FirstToken", "MarkdownToken", "SecondToken"),
    ]


def test_git_character_diff_bounds_repetitive_input_and_fails_closed(
    tmp_path: Path,
) -> None:
    """A quadratic matcher would miss the five-second bound on repetitive input."""
    repo = _repo(tmp_path)
    source_path = repo / "repetitive.ts"
    prefix = b"a " * 50_000
    before = prefix + b"ExactToken\n"
    after = prefix + b"ExactT0ken\n"
    source_path.write_bytes(before)
    _git(repo, "add", source_path.name)
    _git(repo, "commit", "-m", "add repetitive parser fixture")
    left = _git(repo, "rev-parse", "HEAD")
    source_path.write_bytes(after)
    _git(repo, "commit", "-am", "edit repetitive parser fixture")

    started = time.monotonic()
    old_ranges, new_ranges = customization_checker._git_changed_byte_ranges(
        repo, left, "HEAD", source_path.name, before, after
    )

    assert time.monotonic() - started < 5
    assert old_ranges == [(len(prefix) + 6, len(prefix) + 7)]
    assert new_ranges == [(len(prefix) + 6, len(prefix) + 7)]


def test_git_character_diff_handles_blank_line_insertion(tmp_path: Path) -> None:
    """A porcelain newline record must not make valid source fail closed."""
    repo = _repo(tmp_path)
    source_path = repo / "blank-line.ts"
    before = b"abc\n\ndef\n"
    after = b"abc\nX\ndef\n"
    source_path.write_bytes(before)
    _git(repo, "add", source_path.name)
    _git(repo, "commit", "-m", "add blank-line fixture")
    left = _git(repo, "rev-parse", "HEAD")
    source_path.write_bytes(after)
    _git(repo, "commit", "-am", "fill blank line")

    old_ranges, new_ranges = customization_checker._git_changed_byte_ranges(
        repo, left, "HEAD", source_path.name, before, after
    )

    assert old_ranges == []
    assert new_ranges == [(4, 5)]


def test_git_character_diff_skips_git_for_identical_endpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An rc=1 no-change stream must not override identical endpoint bytes."""
    captures = 0

    def no_change_capture(*_args, **_kwargs):
        nonlocal captures
        captures += 1
        return customization_checker._CompletedCapture(
            returncode=1,
            stdout=b"@@ -1 +1 @@\n 61\n~\n",
            stderr=b"",
        )

    monkeypatch.setattr(
        customization_checker, "_run_bounded_capture", no_change_capture
    )

    assert customization_checker._git_changed_byte_ranges(
        tmp_path,
        "a" * 40,
        "b" * 40,
        "owned.ts",
        b"a",
        b"a",
    ) == ([], [])
    assert captures == 0


def test_typescript_import_reflow_keeps_unchanged_owned_symbol_same_file(
    tmp_path: Path,
) -> None:
    """Line reflow around an unchanged import must not report an owned hit."""
    before = (
        "import { addWorktree, listWorktrees } from './git-worktree-ops'\n"
        "void addWorktree\n"
    )
    after = (
        "import {\n"
        "  addWorktree,\n"
        "  listWorktrees,\n"
        "} from './git-worktree-ops'\n"
        "void addWorktree\n"
    )
    repo = _repo(tmp_path)
    source_path = repo / "owned.ts"
    source_path.write_text(before)
    _git(repo, "add", source_path.name)
    _git(repo, "commit", "-m", "add single-line import")
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    raw = yaml.safe_load(manifest.read_text())
    entry = raw["upstream_changes"][0]
    entry["files"] = [source_path.name]
    entry["owned_symbols"] = ["addWorktree"]
    entry["overlap_policy"] = "owned_symbol"
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))
    entry = load_and_validate_manifest(manifest, repo, check_git=False)[
        "upstream_changes"
    ][0]
    source_path.write_text(after)
    _git(repo, "commit", "-am", "reflow import")

    overlap = classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")

    assert overlap["classification"] == "same_file"


def test_git_character_diff_builds_each_endpoint_line_table_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rebuilding a 100 KiB line table for every hunk is superlinear work."""
    repo = _repo(tmp_path)
    source_path = repo / "many-hunks.ts"
    before_lines = [
        f"{line:04d} ".encode("ascii") + (b"a" * 90) + b" X\n"
        for line in range(1024)
    ]
    after_lines = list(before_lines)
    for line in range(8, 1024, 16):
        after_lines[line] = after_lines[line][:-2] + b"Y\n"
    before = b"".join(before_lines)
    after = b"".join(after_lines)
    source_path.write_bytes(before)
    _git(repo, "add", source_path.name)
    _git(repo, "commit", "-m", "add many-hunk fixture")
    left = _git(repo, "rev-parse", "HEAD")
    source_path.write_bytes(after)
    _git(repo, "commit", "-am", "edit separated characters")
    real_boundaries = customization_checker._byte_line_boundaries
    constructed_for: list[bytes] = []

    def counted_boundaries(source: bytes) -> list[int]:
        constructed_for.append(source)
        return real_boundaries(source)

    monkeypatch.setattr(
        customization_checker, "_byte_line_boundaries", counted_boundaries
    )

    old_ranges, new_ranges = customization_checker._git_changed_byte_ranges(
        repo, left, "HEAD", source_path.name, before, after
    )

    assert len(old_ranges) == 64
    assert len(new_ranges) == 64
    assert all(end - start == 1 for start, end in old_ranges)
    assert all(end - start == 1 for start, end in new_ranges)
    assert constructed_for == [
        before.hex().encode("ascii") + b"\n",
        after.hex().encode("ascii") + b"\n",
    ]


def test_git_character_diff_rejects_malformed_hunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trusting malformed porcelain offsets could suppress an owned overlap."""
    monkeypatch.setattr(
        customization_checker,
        "_run_bounded_capture",
        lambda *_args, **_kwargs: customization_checker._CompletedCapture(
            returncode=1,
            stdout=b"@@ -1 +1 @@\n-not-the-source\n+ExactToken\n~\n",
            stderr=b"",
        ),
    )

    with pytest.raises(ValueError, match="malformed Git character diff"):
        customization_checker._git_changed_byte_ranges(
            tmp_path,
            "a" * 40,
            "b" * 40,
            "owned.ts",
            b"ExactToken\n",
            b"ExactT0ken\n",
        )


def test_git_character_diff_rejects_hunk_that_omits_changed_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A one-sided hunk must not hide changed bytes on the omitted endpoint."""
    monkeypatch.setattr(
        customization_checker,
        "_run_bounded_capture",
        lambda *_args, **_kwargs: customization_checker._CompletedCapture(
            returncode=1,
            stdout=b"@@ -1 +0,0 @@\n-61\n~\n",
            stderr=b"",
        ),
    )

    with pytest.raises(ValueError, match="malformed Git character diff"):
        customization_checker._git_changed_byte_ranges(
            tmp_path,
            "a" * 40,
            "b" * 40,
            "owned.ts",
            b"a",
            b"ExactToken",
        )


@pytest.mark.parametrize(
    ("before", "after", "expected_old", "expected_new"),
    [
        (b"", b"a", [], [(0, 1)]),
        (b"a", b"", [(0, 1)], []),
    ],
    ids=("insert-into-empty", "delete-to-empty"),
)
def test_git_character_diff_preserves_empty_endpoint_changes(
    tmp_path: Path,
    before: bytes,
    after: bytes,
    expected_old: list[tuple[int, int]],
    expected_new: list[tuple[int, int]],
) -> None:
    """Exact endpoint coverage must still accept real insertions and deletions."""
    repo = _repo(tmp_path)
    source_path = repo / "empty-endpoint.ts"
    source_path.write_bytes(before)
    _git(repo, "add", source_path.name)
    _git(repo, "commit", "-m", "add endpoint fixture")
    left = _git(repo, "rev-parse", "HEAD")
    source_path.write_bytes(after)
    _git(repo, "commit", "-am", "change endpoint fixture")

    old_ranges, new_ranges = customization_checker._git_changed_byte_ranges(
        repo, left, "HEAD", source_path.name, before, after
    )

    assert old_ranges == expected_old
    assert new_ranges == expected_new


def test_git_character_diff_uses_lf_only_for_source_line_boundaries(
    tmp_path: Path,
) -> None:
    """Treating JavaScript whitespace as a line break corrupts Git hunk offsets."""
    repo = _repo(tmp_path)
    source_path = repo / "whitespace.ts"
    before = b"const ExactToken = 1;\vconst other = 1;\r\n"
    after = b"const ExactToken = 1;\vconst other = 2;\r\n"
    source_path.write_bytes(before)
    _git(repo, "add", source_path.name)
    _git(repo, "commit", "-m", "add non-line JavaScript whitespace")
    left = _git(repo, "rev-parse", "HEAD")
    source_path.write_bytes(after)
    _git(repo, "commit", "-am", "edit after non-line whitespace")
    changed = before.index(b"1", before.index(b"other"))

    old_ranges, new_ranges = customization_checker._git_changed_byte_ranges(
        repo, left, "HEAD", source_path.name, before, after
    )

    assert old_ranges == [(changed, changed + 1)]
    assert new_ranges == [(changed, changed + 1)]


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (b"A\rB1\rC", b"A\rB2\rC", [(3, 4)]),
        (
            b"A\rB1\r\nkeep\nC\rD1",
            b"A\rB2\r\nkeep\nC\rD2",
            [(3, 4), (14, 15)],
        ),
    ],
    ids=("cr-only", "mixed-line-endings"),
)
def test_git_character_diff_uses_lf_only_for_git_hunk_lines(
    tmp_path: Path,
    before: bytes,
    after: bytes,
    expected: list[tuple[int, int]],
) -> None:
    """Lone CR bytes are source content, never Git hunk line boundaries."""
    repo = _repo(tmp_path)
    source_path = repo / "line-endings.ts"
    source_path.write_bytes(before)
    _git(repo, "add", source_path.name)
    _git(repo, "commit", "-m", "add line-ending fixture")
    left = _git(repo, "rev-parse", "HEAD")
    source_path.write_bytes(after)
    _git(repo, "commit", "-am", "edit across line endings")

    old_ranges, new_ranges = customization_checker._git_changed_byte_ranges(
        repo, left, "HEAD", source_path.name, before, after
    )

    assert old_ranges == expected
    assert new_ranges == expected


@pytest.mark.parametrize("failure", ["timeout", "output_limit"])
def test_git_character_diff_propagates_bounded_capture_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """A bounded child failure must abort instead of returning empty ranges."""
    def unavailable(*_args, **_kwargs):
        raise customization_checker._BoundedCaptureFailure(failure)

    monkeypatch.setattr(customization_checker, "_run_bounded_capture", unavailable)

    with pytest.raises(ValueError, match=rf"Git character diff {failure}"):
        customization_checker._git_changed_byte_ranges(
            tmp_path,
            "a" * 40,
            "b" * 40,
            "owned.ts",
            b"ExactToken\n",
            b"ExactT0ken\n",
        )


def test_non_python_parser_failure_cannot_fall_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restoring a lexical fallback would hide parser outages from both callers."""
    repo, source_path, manifest = _non_python_manifest(
        tmp_path, ".ts", "const ExactToken = true;\n"
    )
    left = _git(repo, "rev-parse", "HEAD")
    entry = yaml.safe_load(manifest.read_text())["upstream_changes"][0]
    source_path.write_text("const ExactToken = false;\n")
    _git(repo, "commit", "-am", "edit parser fixture")

    def unavailable(_requests):
        raise ValueError("non-Python parser unavailable")

    monkeypatch.setattr(customization_checker, "_run_parser_batch", unavailable)
    with pytest.raises(ValueError, match="non-Python parser unavailable"):
        load_and_validate_manifest(manifest, repo, check_git=False)
    with pytest.raises(ValueError, match="non-Python parser unavailable"):
        classify_upstream_overlap(entry, repo, f"{left}..HEAD")


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
    _git(repo, "commit", "-m", f"add {suffix} semantic fixture")
    manifest = _manifest(repo, _git(repo, "rev-parse", "HEAD"))
    raw = yaml.safe_load(manifest.read_text())
    entry = raw["upstream_changes"][0]
    entry["files"] = [source_path.name]
    entry["owned_symbols"] = ["ExactToken"]
    entry["overlap_policy"] = "owned_symbol"
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))
    return repo, source_path, manifest


def _assert_javascript_syntax(tmp_path: Path, source: str) -> None:
    """Keep parser fixtures grammar-valid independently of symbol resolution."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to validate JavaScript parser fixtures")
    fixture = tmp_path / "parser-syntax.mjs"
    fixture.write_text(source, encoding="utf-8")
    checked = subprocess.run(
        [node, "--check", str(fixture)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr


def _assert_typescript_syntax(tmp_path: Path, source: str) -> None:
    """Validate the TypeScript-only parser fixture with tsc."""
    node = shutil.which("node")
    tsc = Path(__file__).parents[2] / "node_modules/typescript/bin/tsc"
    if node is None or not tsc.is_file():
        pytest.skip("node and repository TypeScript are required for this fixture")
    fixture = tmp_path / "parser-syntax.ts"
    fixture.write_text(source, encoding="utf-8")
    checked = subprocess.run(
        [node, str(tsc), "--noEmit", "--skipLibCheck", "--target", "ES2022", str(fixture)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr


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
    _assert_javascript_syntax(tmp_path, source)
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
            "const value = `${(() => { for (;;) { /}}/.test('}}'); break; } "
            "/* ExactToken */ return 1; })()}`\n"
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
    _assert_javascript_syntax(tmp_path, source)
    repo, _source, manifest = _non_python_manifest(tmp_path, ".ts", source)

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    "source",
    [
        (
            "const value = `${(() => { while (true) break\n"
            "/}}/.test('}}')\n/* ExactToken */ return 1; })()}`\n"
        ),
        (
            "const value = `${(() => { while (true) break;\n"
            "/}}/.test('}}')\n/* ExactToken */ return 1; })()}`\n"
        ),
        (
            "const value = `${(() => { let count = 0; while (count++ < 1) continue\n"
            "/}}/.test('}}')\n/* ExactToken */ return count; })()}`\n"
        ),
        (
            "const value = `${(() => { let count = 0; while (count++ < 1) continue;\n"
            "/}}/.test('}}')\n/* ExactToken */ return count; })()}`\n"
        ),
        (
            "const value = `${(() => { loop: while (true) break loop;\n"
            "/}}/.test('}}')\n/* ExactToken */ return 1; })()}`\n"
        ),
        (
            "const value = `${(() => { loop: while (true) continue loop;\n"
            "/}}/.test('}}')\n/* ExactToken */ return 1; })()}`\n"
        ),
        (
            "const value = `${(() => { debugger\n"
            "/}}/.test('}}')\n/* ExactToken */ return 1; })()}`\n"
        ),
        (
            "const value = `${(() => { debugger;\n"
            "/}}/.test('}}')\n/* ExactToken */ return 1; })()}`\n"
        ),
        (
            "const value = `${(() => { label: { }\n"
            "/}}/.test('}}')\n/* ExactToken */ return 1; })()}`\n"
        ),
    ],
)
def test_typescript_statement_and_label_regex_goals_do_not_expose_comments(
    tmp_path: Path,
    source: str,
) -> None:
    """ASI, labels, and statement closers all leave the next slash regex-eligible."""
    _assert_javascript_syntax(tmp_path, source)
    repo, _source, manifest = _non_python_manifest(tmp_path, ".ts", source)

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    ("jump", "label", "boundary"),
    [
        ("break", "loop", "\n"),
        ("continue", "loop", "\n"),
        ("break", "of", "\n"),
        ("continue", "get", "\n"),
        ("break", "loop", ";\n"),
        ("continue", "async", "; "),
    ],
)
def test_typescript_jump_label_stays_restricted_until_statement_boundary(
    tmp_path: Path,
    jump: str,
    label: str,
    boundary: str,
) -> None:
    """An optional label remains in the jump statement through ASI or ``;``."""
    condition = "true" if jump == "break" else "false"
    source = (
        f"const value = `${{(() => {{ {label}: while ({condition}) "
        f"{jump} {label}{boundary}"
        "/}}/.test('}}')\n/* ExactToken */ return 1; })()}`\n"
    )
    _assert_javascript_syntax(tmp_path, source)
    repo, _source, manifest = _non_python_manifest(tmp_path, ".ts", source)

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_typescript_unlabeled_break_asi_does_not_capture_next_division(
    tmp_path: Path,
) -> None:
    """A post-ASI identifier belongs to a new expression, not a jump label."""
    source = (
        "let independent = 8;\n"
        "while (true) break\n"
        "independent / 2; /* ExactToken */\n"
    )
    _assert_javascript_syntax(tmp_path, source)
    repo, _source, manifest = _non_python_manifest(tmp_path, ".ts", source)

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    "source",
    [
        "let of = 8; loop: while (true) break loop\nof / 2; /* ExactToken */\n",
        "let of = 8; loop: while (true) break loop;\nof / 2; /* ExactToken */\n",
        (
            "let of = 8; loop: while (true) { continue loop\n"
            "of / 2; /* ExactToken */\n}\n"
        ),
        (
            "let of = 8; loop: while (true) { continue loop;\n"
            "of / 2; /* ExactToken */\n}\n"
        ),
        "let of = 8; debugger\nof / 2; /* ExactToken */\n",
        "let of = 8; debugger;\nof / 2; /* ExactToken */\n",
        (
            "let of = 8; function divide() { return\n"
            "of / 2; /* ExactToken */\n}\n"
        ),
        (
            "let of = 8; function divide() { return;\n"
            "of / 2; /* ExactToken */\n}\n"
        ),
        "let of = 8; const quotient = of / 2; /* ExactToken */\n",
        "const of = () => 8; of() / 2; /* ExactToken */\n",
        "const value = { of: 8 }; value.of / 2; /* ExactToken */\n",
        "let of = 8; of++ / 2; /* ExactToken */\n",
        "const values = [8]; let of = 0; values[of] / 2; /* ExactToken */\n",
        "let of = 8; for (of / 2; of < 9; of++) {} /* ExactToken */\n",
        "for (let of = 8; of < 9; of++) {} /* ExactToken */\n",
    ],
)
def test_typescript_contextual_of_keeps_ordinary_division_goal(
    tmp_path: Path,
    source: str,
) -> None:
    """Outside a for-of delimiter, ``of`` remains an ordinary identifier."""
    _assert_javascript_syntax(tmp_path, source)
    repo, _source, manifest = _non_python_manifest(tmp_path, ".ts", source)

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    "source",
    [
        (
            "const value = `${(() => { for (const entry of /}}/.exec('}}') ?? []) {} "
            "/* ExactToken */ return 1; })()}`\n"
        ),
        (
            "const value = `${(async () => { for await (const entry of /}}/.exec('}}') ?? []) {} "
            "/* ExactToken */ return 1; })()}`\n"
        ),
        (
            "const value = `${(() => { for (const outer of [1]) { "
            "for (const entry of /}}/.exec('}}') ?? []) {} } "
            "/* ExactToken */ return 1; })()}`\n"
        ),
        (
            "const value = `${(() => { for (const entry of ((/}}/.exec('}}') ?? []))) {} "
            "/* ExactToken */ return 1; })()}`\n"
        ),
        (
            "const value = `${(() => { for (let of of /}}/.exec('}}') ?? []) {} "
            "/* ExactToken */ return 1; })()}`\n"
        ),
    ],
)
def test_typescript_for_of_delimiter_keeps_regex_goal_inside_templates(
    tmp_path: Path,
    source: str,
) -> None:
    """Only a real for-of delimiter may make its RHS slash a regex literal."""
    _assert_javascript_syntax(tmp_path, source)
    repo, _source, manifest = _non_python_manifest(tmp_path, ".ts", source)

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    "source",
    [
        (
            "const value = `${(() => { for ((entry) of /}}/.exec('}}') ?? []) {} "
            "/* ExactToken */ return 1; })()}`\n"
        ),
        (
            "const value = `${(async () => { for await ((entry) of /}}/.exec('}}') ?? []) {} "
            "/* ExactToken */ return 1; })()}`\n"
        ),
        (
            "const value = `${(() => { for (const [entry = value => { return 1; }] "
            "of /}}/.exec('}}') ?? []) {} /* ExactToken */ return 1; })()}`\n"
        ),
        (
            "const value = `${(() => { for (const { entry = function nested() { return 1; } } "
            "of /}}/.exec('}}') ?? []) {} /* ExactToken */ return 1; })()}`\n"
        ),
        (
            "const value = `${(() => { for (const { entry = class { method() { return 1; } } } "
            "of /}}/.exec('}}') ?? []) {} /* ExactToken */ return 1; })()}`\n"
        ),
        (
            "const value = `${(() => { for (const [{ entry: [value = 1] }] "
            "of /}}/.exec('}}') ?? []) {} /* ExactToken */ return 1; })()}`\n"
        ),
    ],
)
def test_typescript_nested_for_lhs_keeps_outer_delimiter_at_header_level(
    tmp_path: Path,
    source: str,
) -> None:
    """Closed LHS groups complete the header without exposing its regex RHS."""
    _assert_javascript_syntax(tmp_path, source)
    repo, _source, manifest = _non_python_manifest(tmp_path, ".ts", source)

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    "source",
    [
        (
            "for (const key in function divide() { return 1\n"
            "of / /* ExactToken */ 2 / 3; }) {}\n"
        ),
        (
            "for (const key in value => { return 1\n"
            "of / /* ExactToken */ 2 / 3; }) {}\n"
        ),
        (
            "for (const key in class Divider { method() { return 1\n"
            "of / /* ExactToken */ 2 / 3; } }) {}\n"
        ),
    ],
)
def test_typescript_nested_for_rhs_bodies_do_not_claim_outer_of_delimiter(
    tmp_path: Path,
    source: str,
) -> None:
    """An ``of`` in a nested RHS body remains an ordinary division operand."""
    _assert_javascript_syntax(tmp_path, source)
    repo, _source, manifest = _non_python_manifest(tmp_path, ".ts", source)

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_typescript_nested_for_headers_use_the_innermost_context(
    tmp_path: Path,
) -> None:
    """An inner await header owns its LHS and regex RHS until its own close."""
    source = (
        "const value = `${(async () => { for (const outer of [1]) { for await "
        "(const [entry = value => { return 1; }] of (/}}/.exec('}}') ?? [])) {} } "
        "/* ExactToken */ return 1; })()}`\n"
    )
    _assert_javascript_syntax(tmp_path, source)
    repo, _source, manifest = _non_python_manifest(tmp_path, ".ts", source)

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    "source",
    [
        "const value = `${(() => { for ((entry) of /}}/.exec('}}') /* ExactToken */",
        (
            "for (const key in function divide() { return 1\n"
            "of / /* ExactToken */ 2 / 3;"
        ),
    ],
)
def test_typescript_malformed_syntax_fails_closed(
    tmp_path: Path,
    source: str,
) -> None:
    """Malformed TypeScript cannot downgrade into best-effort token matching."""
    repo, _source, manifest = _non_python_manifest(tmp_path, ".ts", source)

    with pytest.raises(
        ValueError,
        match='non-Python parser parse_diagnostic at "owned\\.ts"',
    ):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_typescript_postfix_non_null_division_does_not_expose_comments(
    tmp_path: Path,
) -> None:
    """TypeScript ``!`` after an expression must not turn division into regex."""
    source = (
        "const value: number = 4;\n"
        "const rendered = `${(() => { const quotient = value! / 2; "
        "/}}/.test('}}'); /* ExactToken */ return quotient; })()}`;\n"
    )
    _assert_typescript_syntax(tmp_path, source)
    repo, _source, manifest = _non_python_manifest(tmp_path, ".ts", source)

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_typescript_unary_not_keeps_regex_goal_and_hides_its_literal_comment(
    tmp_path: Path,
) -> None:
    """Unary ``!`` still starts an expression, unlike postfix TypeScript ``!``."""
    source = (
        "const rendered = `${(() => { const matched = ! /}}/.test('}}'); "
        "/* ExactToken */ return matched; })()}`;\n"
    )
    _assert_javascript_syntax(tmp_path, source)
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
    _assert_javascript_syntax(tmp_path, source)
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


@pytest.mark.parametrize(
    "source",
    [
        "- ```html\n<!-- ExactToken -->\n",
        "> ```html\n<!-- ExactToken -->\n",
        "> - ```html\n<!-- ExactToken -->\n",
        "100. ```html\n<!-- ExactToken -->\n",
        "-\t```html\n<!-- ExactToken -->\n",
        "- ```html\n> <!-- ExactToken -->\n",
        "- ```html\nlazy continuation <!-- ExactToken -->\n",
    ],
)
def test_markdown_contained_fence_closes_and_reprocesses_outdented_line(
    tmp_path: Path,
    source: str,
) -> None:
    """A list/quote fence cannot hide an ordinary comment after its container ends."""
    repo, _source, manifest = _non_python_manifest(tmp_path, ".md", source)

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_markdown_contained_fence_keeps_blank_continuation_literal(
    tmp_path: Path,
) -> None:
    """A blank line inside a list fence does not close it before its indented body."""
    repo, _source, manifest = _non_python_manifest(
        tmp_path,
        ".md",
        "- ```html\n\n  <!-- ExactToken -->\n  ```\n",
    )

    load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize("marker_only", [">", "> "])
def test_markdown_marker_only_blockquote_blank_keeps_list_fence_literal(
    tmp_path: Path,
    marker_only: str,
) -> None:
    """A continued blockquote marker can carry a blank nested-list fence line."""
    repo, _source, manifest = _non_python_manifest(
        tmp_path,
        ".md",
        (
            "> - ```html\n"
            f"{marker_only}\n"
            ">   <!-- ExactToken -->\n"
            ">   ```\n"
        ),
    )

    load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    "source",
    [
        (
            "> > - ```html\n"
            "> >\n"
            "> >   <!-- ExactToken -->\n"
            "> >   ```\n"
        ),
        (
            "> - 10. ```html\n"
            ">\n"
            ">       <!-- ExactToken -->\n"
            ">       ```\n"
        ),
    ],
)
def test_markdown_marker_only_blank_continues_nested_containers(
    tmp_path: Path,
    source: str,
) -> None:
    repo, _source, manifest = _non_python_manifest(tmp_path, ".md", source)

    load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    ("opening", "blank", "continuation"),
    [
        ("- ", "  ", "  "),
        ("10. ", "    ", "    "),
        ("-\t", "\t", "\t"),
    ],
)
def test_markdown_list_only_blank_indentation_keeps_fence_literal(
    tmp_path: Path,
    opening: str,
    blank: str,
    continuation: str,
) -> None:
    source = (
        f"{opening}```html\n"
        f"{blank}\n"
        f"{continuation}<!-- ExactToken -->\n"
        f"{continuation}```\n"
    )
    repo, _source, manifest = _non_python_manifest(tmp_path, ".md", source)

    load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize("blank", ["", "   ", " \t"])
@pytest.mark.parametrize(
    "following",
    ["<!-- ExactToken -->", ">   <!-- ExactToken -->"],
)
def test_markdown_unmarked_blank_terminates_blockquote_contained_fence(
    tmp_path: Path,
    blank: str,
    following: str,
) -> None:
    """A blank without ``>`` ends the quote before prefixed or outer HTML."""
    repo, _source, manifest = _non_python_manifest(
        tmp_path,
        ".md",
        f"> - ```html\n{blank}\n{following}\n",
    )

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_markdown_marker_only_blank_does_not_hide_later_outer_comment(
    tmp_path: Path,
) -> None:
    repo, _source, manifest = _non_python_manifest(
        tmp_path,
        ".md",
        "> - ```html\n>\n<!-- ExactToken -->\n",
    )

    with pytest.raises(ValueError, match="ExactToken.*declared files"):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_markdown_root_fence_keeps_raw_blank_literal_until_eof(tmp_path: Path) -> None:
    repo, _source, manifest = _non_python_manifest(
        tmp_path,
        ".md",
        "```html\n\n<!-- ExactToken -->\n",
    )

    load_and_validate_manifest(manifest, repo, check_git=False)


@pytest.mark.parametrize(
    "source",
    [
        "```html\n<!-- ExactToken -->\n```\n",
        "~~~html\n<!-- ExactToken -->\n",
        "- ````html\n  <!-- ExactToken -->\n  ```\n  <!-- ExactToken -->\n  ````\n",
    ],
)
def test_markdown_root_eof_and_width_controls_preserve_fence_literals(
    tmp_path: Path,
    source: str,
) -> None:
    repo, _source, manifest = _non_python_manifest(tmp_path, ".md", source)

    load_and_validate_manifest(manifest, repo, check_git=False)


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


# ── Security-critical ownership fixture (review finding LOW-008) ─────────────
#
# The checker validates each machine-locatable symbol against the declared
# files at the requested Git revision. What it cannot derive is the reverse:
# an owner silently DELETED from the ledger while still load-bearing in code.
# That mutation
# (`_navigation_session_key` removed from owned_symbols) exited 0, leaving the
# highest-churn merge contract dependent on reviewer memory.
#
# This fixture pins it. Each name below is a routing decision, a mutable-state
# owner, or one of the six SSRF guard-forcing sites -- losing any of them to an
# upstream merge silently un-scopes corporate-browser trust with no build error.
# If a customization is genuinely retired, delete its line here in the SAME
# commit and say why.

_BROWSER_PROFILE_CRITICAL_OWNERS = {
    "enrolled-browser-launch-wiring": {
        # Routing: which browser drives which URL.
        "_navigation_session_key",
        "_is_enrolled_session_key",
        "_session_browser_profile",
        "_ENROLLED_SUFFIX",
        # The six guard-forcing sites.
        "_eval_ssrf_guard_active",
        "browser_navigate",
        "browser_snapshot",
        "browser_vision",
        # Per-session endpoint state + its lifecycle.
        "_session_cdp_url",
        "_session_cdp_urls",
        "_session_handles",
        "_release_session_handle",
        "cleanup_browser",
        "_cleanup_single_browser_session",
    },
}


def test_ledger_still_declares_every_security_critical_owner() -> None:
    repo = Path(__file__).resolve().parents[2]
    manifest = repo / "docs/upstream-customizations/browser-profiles.yaml"
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    entries = {
        entry["id"]: entry
        for entry in data["upstream_changes"]
        if isinstance(entry, dict)
    }
    for entry_id, required in _BROWSER_PROFILE_CRITICAL_OWNERS.items():
        assert entry_id in entries, f"ledger entry {entry_id!r} disappeared"
        declared = set(entries[entry_id].get("owned_symbols", []))
        missing = sorted(required - declared)
        assert not missing, (
            f"{entry_id}.owned_symbols no longer declares {missing}. These are "
            "routing/guard/lifecycle owners; dropping one from the ledger "
            "downgrades an upstream rewrite of it from an owned-symbol hit to "
            "the weakest same-file signal, with no other failure."
        )


def test_strict_requested_revision_loads_its_committed_manifest(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    _git(repo, "add", "ledger.yaml")
    _git(repo, "commit", "-m", "add historical ledger")
    requested = _git(repo, "rev-parse", "HEAD")

    (repo / "future.py").write_text("class FutureOwned:\n    pass\n")
    newer = yaml.safe_load(manifest.read_text())
    newer["upstream_changes"][0]["files"] = ["future.py"]
    newer["upstream_changes"][0]["owned_symbols"] = ["FutureOwned"]
    manifest.write_text(yaml.safe_dump(newer, sort_keys=False))
    _git(repo, "add", "future.py", "ledger.yaml")
    _git(repo, "commit", "-m", "replace ledger after requested revision")

    loaded = load_and_validate_manifest(
        manifest,
        repo,
        source_revision=requested,
        strict=True,
    )

    assert loaded["upstream_changes"][0]["files"] == ["core.py"]
    assert loaded["upstream_changes"][0]["owned_symbols"] == ["Owned"]


def test_strict_requested_revision_cannot_be_satisfied_by_dirty_manifest(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    historical = yaml.safe_load(manifest.read_text())
    historical["upstream_changes"][0]["owned_symbols"] = ["MissingHistorically"]
    manifest.write_text(yaml.safe_dump(historical, sort_keys=False))
    _git(repo, "add", "ledger.yaml")
    _git(repo, "commit", "-m", "add historically invalid ledger")
    requested = _git(repo, "rev-parse", "HEAD")

    dirty = yaml.safe_load(manifest.read_text())
    dirty["upstream_changes"][0]["owned_symbols"] = ["Owned"]
    manifest.write_text(yaml.safe_dump(dirty, sort_keys=False))

    with pytest.raises(ValueError, match="MissingHistorically.*declared files"):
        load_and_validate_manifest(
            manifest,
            repo,
            source_revision=requested,
            strict=True,
        )


def test_strict_manifest_path_must_be_repository_contained(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    _git(repo, "add", "ledger.yaml")
    _git(repo, "commit", "-m", "add historical ledger")
    requested = _git(repo, "rev-parse", "HEAD")
    outside_manifest = tmp_path / "outside.yaml"
    outside_manifest.write_bytes(manifest.read_bytes())

    with pytest.raises(ValueError, match="manifest path is not repository-contained"):
        load_and_validate_manifest(
            outside_manifest,
            repo,
            source_revision=requested,
            strict=True,
        )


def test_non_strict_manifest_loading_keeps_using_checkout_bytes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    _git(repo, "add", "ledger.yaml")
    _git(repo, "commit", "-m", "add ledger")
    manifest.write_text("schema_version: 2\n")

    with pytest.raises(ValueError, match="manifest schema_version must be 1"):
        load_and_validate_manifest(manifest, repo, strict=False)


def test_strict_cli_historical_manifest_ignores_invalid_checkout_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    _git(repo, "add", "ledger.yaml")
    _git(repo, "commit", "-m", "add historical ledger")
    requested = _git(repo, "rev-parse", "HEAD")
    manifest.write_bytes(b"\xff checkout bytes are not UTF-8\n")
    monkeypatch.chdir(repo)

    assert customization_checker.main(
        [
            "--manifest",
            str(manifest),
            "--strict",
            "--base-ref",
            requested,
        ]
    ) == 0


def test_cli_set_verified_upstream_still_updates_checkout_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    _git(repo, "commit", "--allow-empty", "-m", "new verification point")
    verified = _git(repo, "rev-parse", "HEAD")
    monkeypatch.chdir(repo)

    assert customization_checker.main(
        [
            "--manifest",
            str(manifest),
            "--set-verified-upstream",
            verified,
        ]
    ) == 0
    updated = yaml.safe_load(manifest.read_text())
    assert updated["upstream_changes"][0]["last_verified_upstream"] == verified


def test_strict_cli_validates_owned_symbols_at_requested_base_ref(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    _git(repo, "add", "ledger.yaml")
    _git(repo, "commit", "-m", "add ledger")
    requested = _git(repo, "rev-parse", "HEAD")
    (repo / "core.py").write_text("class Replacement:\n    pass\n")
    _git(repo, "commit", "-am", "replace owned definition")
    monkeypatch.chdir(repo)

    assert customization_checker.main(
        [
            "--manifest",
            str(manifest),
            "--strict",
            "--base-ref",
            requested,
        ]
    ) == 0
    assert customization_checker.main(
        ["--manifest", str(manifest), "--strict", "--base-ref", "HEAD"]
    ) == 1
    assert "does not exist in declared files" in capsys.readouterr().err


def test_cli_report_publication_is_atomic_and_preserves_acknowledgements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parser failure cannot truncate the prior report; success replaces it."""
    repo, source_path, manifest = _non_python_manifest(
        tmp_path, ".ts", 'const ExactToken = "ExactToken";\n'
    )
    left = _git(repo, "rev-parse", "HEAD")
    source_path.write_text('const ExactToken = "ExactT0ken";\n')
    _git(repo, "commit", "-am", "edit owned parser token")
    report_path = repo / "overlap-report.json"
    prior = {
        "schema_version": 1,
        "range": "prior",
        "overlaps": [{"id": "owned", "acknowledged": True}],
    }
    prior_bytes = (json.dumps(prior, indent=2) + "\n").encode("utf-8")
    report_path.write_bytes(prior_bytes)
    report_path.chmod(0o644)

    def exact_results(
        requests: list[customization_checker._ParserRequest],
    ) -> dict[str, dict[str, list[tuple[int, int]]]]:
        results: dict[str, dict[str, list[tuple[int, int]]]] = {}
        for request in requests:
            symbol_results: dict[str, list[tuple[int, int]]] = {}
            for symbol in request.symbols:
                token = symbol.encode("utf-8")
                symbol_results[symbol] = [
                    (start, start + len(token))
                    for start in range(len(request.source))
                    if request.source.startswith(token, start)
                ]
            results[request.request_id] = symbol_results
        return results

    calls = 0

    def fail_classification(requests):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("non-Python parser unavailable")
        return exact_results(requests)

    monkeypatch.chdir(repo)
    monkeypatch.setattr(customization_checker, "_run_parser_batch", fail_classification)
    args = [
        "--manifest",
        str(manifest),
        "--upstream-diff",
        f"{left}..HEAD",
        "--report",
        str(report_path),
    ]

    assert customization_checker.main(args) == 1
    assert report_path.read_bytes() == prior_bytes

    monkeypatch.setattr(customization_checker, "_run_parser_batch", exact_results)
    assert customization_checker.main(args) == 2
    published = json.loads(report_path.read_text())
    assert published["overlaps"][0]["acknowledged"] is True
    assert report_path.stat().st_mode & 0o777 == 0o600


def test_atomic_report_publication_replaces_without_fchmod_on_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = tmp_path / "report.json"
    report.write_text("prior\n")
    chmod_calls: list[tuple[Path, int]] = []
    real_chmod = customization_checker.os.chmod

    def recording_chmod(path, mode):
        chmod_calls.append((Path(path), mode))
        real_chmod(path, mode)

    monkeypatch.delattr(customization_checker.os, "fchmod")
    monkeypatch.setattr(customization_checker.os, "chmod", recording_chmod)
    monkeypatch.setattr(customization_checker, "_is_windows", lambda: True)

    customization_checker._write_json_atomically(report, {"status": "new"})

    assert json.loads(report.read_text()) == {"status": "new"}
    assert len(chmod_calls) == 1
    assert chmod_calls[0][0].parent == tmp_path
    assert chmod_calls[0][1] == 0o600
    assert list(tmp_path.glob(".report.json.*")) == []


def test_atomic_report_publication_cleans_temp_after_replace_failure_without_fchmod(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = tmp_path / "report.json"
    report.write_bytes(b"prior evidence\n")
    monkeypatch.delattr(customization_checker.os, "fchmod")

    def fail_replace(_source, _destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(customization_checker.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failure"):
        customization_checker._write_json_atomically(report, {"status": "new"})

    assert report.read_bytes() == b"prior evidence\n"
    assert list(tmp_path.glob(".report.json.*")) == []


def test_strict_requested_revision_uses_its_committed_path_inventory(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    _git(repo, "add", "ledger.yaml")
    _git(repo, "commit", "-m", "add ledger")
    requested = _git(repo, "rev-parse", "HEAD")
    (repo / "core.py").unlink()
    _git(repo, "commit", "-am", "delete owned file after requested revision")

    load_and_validate_manifest(
        manifest,
        repo,
        source_revision=requested,
        strict=True,
    )

    future = repo / "future.py"
    data = yaml.safe_load(manifest.read_text())
    data["upstream_changes"][0]["files"] = ["future.py"]
    data["upstream_changes"][0]["owned_symbols"] = ["FutureOwned"]
    manifest.write_text(yaml.safe_dump(data, sort_keys=False))
    _git(repo, "add", "ledger.yaml")
    _git(repo, "commit", "-m", "declare a not-yet-existing owned file")
    future_requested = _git(repo, "rev-parse", "HEAD")
    future.write_text("class FutureOwned:\n    pass\n")
    _git(repo, "add", "future.py")
    _git(repo, "commit", "-m", "add future owned file")

    with pytest.raises(ValueError, match="future.py.*source revision"):
        load_and_validate_manifest(
            manifest,
            repo,
            source_revision=future_requested,
            strict=True,
        )


def test_strict_requested_revision_ignores_current_checkout_symlink(
    tmp_path: Path,
) -> None:
    """A later checkout symlink must not redefine an older commit's path."""
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    _git(repo, "add", "ledger.yaml")
    _git(repo, "commit", "-m", "add ledger")
    requested = _git(repo, "rev-parse", "HEAD")
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
    verified = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, verified)
    _git(repo, "add", "ledger.yaml")
    _git(repo, "commit", "-m", "add historical ledger")
    requested = _git(repo, "rev-parse", "HEAD")
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
    baseline = _git(repo, "rev-parse", "HEAD")
    _git(repo, "commit", "--allow-empty", "-m", "newer verification point")
    newer = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "historical-ledger", baseline)
    manifest = _manifest(repo, newer)
    _git(repo, "add", "ledger.yaml")
    _git(repo, "commit", "-m", "add ledger with sibling baseline")
    requested = _git(repo, "rev-parse", "HEAD")

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
    baseline = _git(repo, "rev-parse", "HEAD")
    _git(repo, "commit", "--allow-empty", "-m", "future coverage point")
    newer = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "historical-ledger", baseline)
    manifest = _manifest(repo, baseline)
    data = yaml.safe_load(manifest.read_text())
    data["coverage"] = {"base_commit": newer, "excluded_commits": []}
    manifest.write_text(yaml.safe_dump(data, sort_keys=False))
    _git(repo, "add", "ledger.yaml")
    _git(repo, "commit", "-m", "add ledger with sibling coverage base")
    requested = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(ValueError, match="coverage base is not an ancestor of source revision"):
        load_and_validate_manifest(
            manifest,
            repo,
            source_revision=requested,
            strict=True,
        )
