"""Bounded native and private-curl HTTP transports for Jira."""

from __future__ import annotations

from contextlib import closing
import errno
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import zlib
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

import httpx

if __package__:
    from ._common.errors import ConnectorError
    from ._common.transport import RequestControl, validate_transport_path
    from .models import JiraAuth, JiraError, TransportResponse
else:
    from _common.errors import ConnectorError
    from _common.transport import RequestControl, validate_transport_path
    from models import JiraAuth, JiraError, TransportResponse


_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "DELETE"})
_MAX_REQUEST_BYTES = 256 * 1024
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_HEADER_BYTES = 64 * 1024
_MAX_STDERR_BYTES = 64 * 1024
_MAX_URL_BYTES = 8192
_MAX_DECODE_CHUNK_BYTES = 64 * 1024
_MAX_DEFLATE_PROBE_BYTES = 64 * 1024
_SAFE_RESPONSE_HEADERS = frozenset(
    {"content-type", "retry-after", "server", "cf-ray", "location"}
)
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")
_CLOUDFLARE_1010 = re.compile(
    rb"(?:error\s*1010|access denied[^<]{0,80}1010)", re.I
)
_DEFAULT_APPROVED_EXECUTABLES = frozenset(
    {
        "/usr/bin/curl",
        "/usr/local/bin/curl",
        "/opt/homebrew/bin/curl",
        r"C:\Windows\System32\curl.exe",
        r"C:\Windows\Sysnative\curl.exe",
    }
)
_LOCAL_CAPACITY_ERRNOS = frozenset(
    value
    for value in (
        getattr(errno, "EDQUOT", None),
        getattr(errno, "EFBIG", None),
        getattr(errno, "ENOSPC", None),
    )
    if value is not None
)


class _JiraTransportFailure(JiraError):
    """Safe transport failure annotated only with request-dispatch provenance."""

    def __init__(self, category: str, *, outcome_uncertain: bool) -> None:
        self.outcome_uncertain = outcome_uncertain
        super().__init__(category)


def _response_header(response: TransportResponse, name: str) -> str:
    lowered = name.lower()
    for key, value in response.headers.items():
        if key.lower() == lowered:
            return value
    return ""


def _cloudflare_1010(response: TransportResponse) -> bool:
    if response.status != 403 or len(response.body) > 8192:
        return False
    server = _response_header(response, "server").lower()
    ray = _response_header(response, "cf-ray")
    content_type = _response_header(response, "content-type").lower()
    return (
        server.startswith("cloudflare")
        and bool(ray)
        and ("text/html" in content_type or "text/plain" in content_type)
        and _CLOUDFLARE_1010.search(response.body) is not None
    )


def _validate_path(path: str) -> None:
    invalid = False
    try:
        validate_transport_path(
            path,
            path_prefix="/rest/api/",
            allow_query=False,
            maximum_bytes=_MAX_URL_BYTES,
            reject_encoded_separators=True,
        )
    except ConnectorError:
        invalid = True
    if invalid:
        raise JiraError("invalid_input")


def _remaining_control(
    control: RequestControl | None, *, outcome_uncertain: bool
) -> float | None:
    if control is None:
        return None
    failure = None
    try:
        return control.remaining(outcome_uncertain=outcome_uncertain)
    except ConnectorError as exc:
        failure = exc.category
    raise _JiraTransportFailure(
        failure, outcome_uncertain=outcome_uncertain
    ) from None


def _local_io(operation):
    failure = None
    try:
        return operation()
    except OSError as exc:
        failure = "capacity" if exc.errno in _LOCAL_CAPACITY_ERRNOS else "transient"
    raise _JiraTransportFailure(failure, outcome_uncertain=False) from None


def _validate_method(method: str) -> None:
    if method not in _ALLOWED_METHODS:
        raise JiraError("invalid_input")


def _try_zlib_decompress(decoder, data: bytes, maximum: int) -> bytes | None:
    try:
        return decoder.decompress(data, maximum)
    except zlib.error:
        return None


class _DeflateCandidate:
    def __init__(self, window_bits: int) -> None:
        self.decoder = zlib.decompressobj(window_bits)
        self.body = bytearray()
        self.invalid = False
        self.probe_exceeded = False

    def feed(
        self,
        data,
        *,
        control: RequestControl | None,
        cancel_check: Callable[[], bool],
    ) -> None:
        pending = data
        while pending and not self.invalid and not self.probe_exceeded:
            _remaining_control(control, outcome_uncertain=True)
            if cancel_check():
                raise _JiraTransportFailure("cancelled", outcome_uncertain=True)
            remaining = _MAX_DEFLATE_PROBE_BYTES - len(self.body)
            maximum = min(_MAX_DECODE_CHUNK_BYTES, remaining + 1)
            decoded = _try_zlib_decompress(self.decoder, pending, maximum)
            if decoded is None:
                self.invalid = True
                self.body.clear()
                return
            next_pending = self.decoder.unconsumed_tail
            if self.decoder.unused_data:
                self.invalid = True
                self.body.clear()
                return
            self.body.extend(decoded)
            if len(self.body) > _MAX_DEFLATE_PROBE_BYTES:
                self.probe_exceeded = True
                return
            if not next_pending:
                return
            if not decoded and len(next_pending) >= len(pending):
                self.invalid = True
                self.body.clear()
                return
            pending = next_pending


def _feed_deflate_candidates(
    candidates: tuple[_DeflateCandidate, _DeflateCandidate],
    data,
    *,
    control: RequestControl | None,
    cancel_check: Callable[[], bool],
) -> _DeflateCandidate | None:
    for candidate in candidates:
        candidate.feed(
            data,
            control=control,
            cancel_check=cancel_check,
        )
    valid = [candidate for candidate in candidates if not candidate.invalid]
    if not valid:
        raise _JiraTransportFailure(
            "invalid_remote_data", outcome_uncertain=True
        )
    if len(valid) == 1:
        return valid[0]
    if any(candidate.probe_exceeded for candidate in valid):
        raise _JiraTransportFailure(
            "invalid_remote_data", outcome_uncertain=True
        )
    return None


def _finish_deflate_candidates(
    candidates: tuple[_DeflateCandidate, _DeflateCandidate],
) -> _DeflateCandidate:
    complete = [
        candidate
        for candidate in candidates
        if not candidate.invalid and candidate.decoder.eof
    ]
    if len(complete) == 1:
        return complete[0]
    if len(complete) == 2 and complete[0].body == complete[1].body:
        return complete[0]
    raise _JiraTransportFailure(
        "invalid_remote_data", outcome_uncertain=True
    )


def _read_native_body(
    response: httpx.Response,
    *,
    control: RequestControl | None,
    cancel_check: Callable[[], bool],
) -> bytes:
    encodings = [
        value.strip().lower()
        for value in response.headers.get_list("content-encoding", split_commas=True)
        if value.strip() and value.strip().lower() != "identity"
    ]
    if len(encodings) > 1 or (encodings and encodings[0] not in {"gzip", "deflate"}):
        raise _JiraTransportFailure(
            "invalid_remote_data", outcome_uncertain=True
        )

    # Injected HTTPX transports may return a Response whose constructor has
    # already decoded and cached a small body. Real streamed network responses
    # take the raw path below; cached bodies can only be bounded after the
    # injecting transport has materialized them.
    if response.is_stream_consumed:
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise _JiraTransportFailure("capacity", outcome_uncertain=True)
        return response.content

    encoding = encodings[0] if encodings else None
    decoder = None
    deflate_candidates = None
    if encoding == "gzip":
        decoder = zlib.decompressobj(zlib.MAX_WBITS | 16)
    elif encoding == "deflate":
        deflate_candidates = (
            _DeflateCandidate(zlib.MAX_WBITS),
            _DeflateCandidate(-zlib.MAX_WBITS),
        )

    body = bytearray()
    raw_chunks = iter(response.iter_raw())
    while True:
        _remaining_control(control, outcome_uncertain=True)
        if cancel_check():
            raise _JiraTransportFailure("cancelled", outcome_uncertain=True)
        try:
            raw_chunk = next(raw_chunks)
        except StopIteration:
            break

        segments = (raw_chunk,)
        if deflate_candidates is not None:
            chosen = _feed_deflate_candidates(
                deflate_candidates,
                raw_chunk,
                control=control,
                cancel_check=cancel_check,
            )
            if chosen is None:
                continue
            decoder = chosen.decoder
            body = chosen.body
            deflate_candidates = None
            segments = (decoder.unconsumed_tail,)

        for segment in segments:
            pending = segment
            while pending:
                _remaining_control(control, outcome_uncertain=True)
                if cancel_check():
                    raise _JiraTransportFailure(
                        "cancelled", outcome_uncertain=True
                    )
                remaining = _MAX_RESPONSE_BYTES - len(body)
                if decoder is None:
                    if len(pending) > remaining:
                        raise _JiraTransportFailure(
                            "capacity", outcome_uncertain=True
                        )
                    body.extend(pending)
                    break

                maximum = min(_MAX_DECODE_CHUNK_BYTES, remaining + 1)
                decoded = _try_zlib_decompress(decoder, pending, maximum)
                if decoded is None:
                    raise _JiraTransportFailure(
                        "invalid_remote_data", outcome_uncertain=True
                    )

                next_pending = decoder.unconsumed_tail
                if decoder.unused_data:
                    raise _JiraTransportFailure(
                        "invalid_remote_data", outcome_uncertain=True
                    )
                if len(decoded) > remaining:
                    raise _JiraTransportFailure(
                        "capacity", outcome_uncertain=True
                    )
                body.extend(decoded)
                if not next_pending:
                    break
                if not decoded and len(next_pending) >= len(pending):
                    raise _JiraTransportFailure(
                        "invalid_remote_data", outcome_uncertain=True
                    )
                pending = next_pending

    if deflate_candidates is not None:
        chosen = _finish_deflate_candidates(deflate_candidates)
        decoder = chosen.decoder
        body = chosen.body
    if decoder is not None and not decoder.eof:
        raise _JiraTransportFailure(
            "invalid_remote_data", outcome_uncertain=True
        )
    return bytes(body)


class CurlTransport:
    """Invoke one exact curl executable with all sensitive material in private files."""

    def __init__(
        self,
        authentication: JiraAuth,
        *,
        cancel_check: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        popen=subprocess.Popen,
        approved_executables=None,
    ) -> None:
        self.auth = authentication
        self._cancel_check = cancel_check or (lambda: False)
        self._clock = clock
        self._popen = popen
        approved = (
            _DEFAULT_APPROVED_EXECUTABLES
            if approved_executables is None
            else frozenset(str(path) for path in approved_executables)
        )
        self.executable = self._validate_executable(
            authentication.curl_executable, approved
        )

    @staticmethod
    def _validate_executable(value: str, approved: frozenset[str]) -> Path:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise JiraError("invalid_configuration")
        path = Path(value)
        if (
            not path.is_absolute()
            or str(path) not in approved
            or path.name.lower() not in {"curl", "curl.exe"}
        ):
            raise JiraError("invalid_configuration")
        try:
            metadata = path.lstat()
        except OSError:
            raise JiraError("invalid_configuration") from None
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or not os.access(path, os.X_OK)
        ):
            raise JiraError("invalid_configuration")
        return path

    def __repr__(self) -> str:
        return f"CurlTransport(origin={self.auth.origin!r}, executable={str(self.executable)!r})"

    def close(self) -> None:
        return None

    @staticmethod
    def _write_private(path: Path, payload: bytes) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    @staticmethod
    def _config_line(name: str, value: str | None = None) -> str:
        if value is None:
            return name
        return f"{name} = {json.dumps(value, ensure_ascii=True)}"

    @staticmethod
    def _build_url(origin: str, path: str, params: Mapping[str, Any] | None) -> str:
        url = f"{origin}{path}"
        if params:
            if not isinstance(params, Mapping):
                raise JiraError("invalid_input")
            try:
                query = urlencode(params, doseq=True)
            except (TypeError, ValueError):
                raise JiraError("invalid_input") from None
            url = f"{url}?{query}"
        if len(url.encode("utf-8")) > _MAX_URL_BYTES:
            raise _JiraTransportFailure("capacity", outcome_uncertain=False)
        return url

    @staticmethod
    def _request_body(json_body: Any | None) -> bytes | None:
        if json_body is None:
            return None
        try:
            body = json.dumps(
                json_body, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise JiraError("invalid_input") from None
        if len(body) > _MAX_REQUEST_BYTES:
            raise _JiraTransportFailure("capacity", outcome_uncertain=False)
        return body

    @staticmethod
    def _terminate(process) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=0.25)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=0.25)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def _communicate(
        self,
        process,
        deadline: float,
        control: RequestControl | None,
    ) -> tuple[bytes, bytes]:
        while True:
            if self._cancel_check():
                self._terminate(process)
                raise _JiraTransportFailure("cancelled", outcome_uncertain=True)
            try:
                _remaining_control(control, outcome_uncertain=True)
            except _JiraTransportFailure:
                self._terminate(process)
                raise
            remaining = deadline - self._clock()
            if remaining <= 0:
                self._terminate(process)
                raise _JiraTransportFailure("deadline", outcome_uncertain=True)
            try:
                stdout, stderr = process.communicate(timeout=min(0.05, remaining))
                return stdout or b"", stderr or b""
            except subprocess.TimeoutExpired:
                continue

    @staticmethod
    def _read_bounded(path: Path, maximum: int) -> bytes:
        try:
            size = path.stat().st_size
        except OSError:
            raise _JiraTransportFailure(
                "invalid_remote_data", outcome_uncertain=True
            ) from None
        if size > maximum:
            raise _JiraTransportFailure("capacity", outcome_uncertain=True)
        try:
            return path.read_bytes()
        except OSError:
            raise _JiraTransportFailure(
                "invalid_remote_data", outcome_uncertain=True
            ) from None

    @staticmethod
    def _parse_headers(raw: bytes, expected_status: int) -> dict[str, str]:
        if len(raw) > _MAX_HEADER_BYTES:
            raise _JiraTransportFailure("capacity", outcome_uncertain=True)
        blocks = [block for block in raw.split(b"\r\n\r\n") if block]
        if not blocks:
            raise _JiraTransportFailure(
                "invalid_remote_data", outcome_uncertain=True
            )
        selected = blocks[-1]
        lines = selected.split(b"\r\n")
        try:
            status_line = lines[0].decode("ascii")
        except UnicodeDecodeError:
            raise _JiraTransportFailure(
                "invalid_remote_data", outcome_uncertain=True
            ) from None
        match = re.fullmatch(r"HTTP/[12](?:\.0|\.1|) ([1-5][0-9]{2})(?: [^\r\n]{0,256})?", status_line)
        if match is None or int(match.group(1)) != expected_status:
            raise _JiraTransportFailure(
                "invalid_remote_data", outcome_uncertain=True
            )
        headers: dict[str, str] = {}
        for raw_line in lines[1:]:
            if not raw_line or raw_line[:1] in {b" ", b"\t"} or b":" not in raw_line:
                raise _JiraTransportFailure(
                    "invalid_remote_data", outcome_uncertain=True
                )
            raw_name, raw_value = raw_line.split(b":", 1)
            try:
                name = raw_name.decode("ascii").lower()
                value = raw_value.decode("utf-8").strip()
            except UnicodeDecodeError:
                raise _JiraTransportFailure(
                    "invalid_remote_data", outcome_uncertain=True
                ) from None
            if (
                _HEADER_NAME.fullmatch(name) is None
                or len(value) > 4096
                or any(ord(character) < 32 and character != "\t" for character in value)
            ):
                raise _JiraTransportFailure(
                    "invalid_remote_data", outcome_uncertain=True
                )
            if name in _SAFE_RESPONSE_HEADERS and name not in headers:
                headers[name] = value
        return headers

    @classmethod
    def _completed_client_error(cls, path: Path) -> TransportResponse | None:
        """Recover only a complete, strict 4xx header block from an interrupted curl."""

        try:
            raw = cls._read_bounded(path, _MAX_HEADER_BYTES)
        except _JiraTransportFailure:
            return None
        if not raw.endswith(b"\r\n\r\n"):
            return None
        blocks = [block for block in raw.split(b"\r\n\r\n") if block]
        if not blocks:
            return None
        status_line = blocks[-1].split(b"\r\n", 1)[0]
        match = re.fullmatch(
            rb"HTTP/[12](?:\.0|\.1|) ([1-5][0-9]{2})(?: [^\r\n]{0,256})?",
            status_line,
        )
        if match is None:
            return None
        status = int(match.group(1))
        if not 400 <= status < 500:
            return None
        try:
            headers = cls._parse_headers(raw, status)
        except _JiraTransportFailure:
            return None
        return TransportResponse(status=status, headers=headers, body=b"")

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        timeout_seconds: float,
    ) -> TransportResponse:
        return self.request_with_controls(
            method,
            path,
            params=params,
            json_body=json_body,
            timeout_seconds=timeout_seconds,
            control=None,
        )

    def request_with_controls(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        timeout_seconds: float,
        control: RequestControl | None,
    ) -> TransportResponse:
        _validate_method(method)
        _validate_path(path)
        if not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 120:
            raise JiraError("invalid_configuration")
        if self._cancel_check():
            raise _JiraTransportFailure("cancelled", outcome_uncertain=False)
        controlled_remaining = _remaining_control(
            control, outcome_uncertain=False
        )
        if controlled_remaining is not None:
            timeout_seconds = min(timeout_seconds, controlled_remaining)
        deadline = self._clock() + float(timeout_seconds)
        authorization = self.auth.authorization
        if (
            not isinstance(authorization, str)
            or not authorization
            or len(authorization) > 8192
            or "\r" in authorization
            or "\n" in authorization
        ):
            raise JiraError("invalid_configuration")
        url = self._build_url(self.auth.origin, path, params)
        request_body = self._request_body(json_body)
        temporary = Path(
            _local_io(lambda: tempfile.mkdtemp(prefix="ericsson-jira-curl-"))
        )
        try:
            _local_io(lambda: os.chmod(temporary, 0o700))
            config_path = temporary / "request.conf"
            body_path = temporary / "request.json"
            header_path = temporary / "response.headers"
            output_path = temporary / "response.body"
            lines = [
                self._config_line("silent"),
                self._config_line("show-error"),
                self._config_line("proxy", ""),
                self._config_line("proto", "=http,https"),
                self._config_line("proto-redir", "-all"),
                self._config_line("request", method),
                self._config_line("url", url),
                self._config_line("header", f"Authorization: {authorization}"),
                self._config_line("header", "Accept: application/json"),
                self._config_line("dump-header", str(header_path)),
                self._config_line("output", str(output_path)),
                self._config_line("write-out", "%{http_code}"),
                self._config_line("max-time", str(timeout_seconds)),
                self._config_line("max-filesize", str(_MAX_RESPONSE_BYTES)),
            ]
            if request_body is not None:
                _local_io(lambda: self._write_private(body_path, request_body))
                lines.extend(
                    [
                        self._config_line("header", "Content-Type: application/json"),
                        self._config_line("data-binary", f"@{body_path}"),
                    ]
                )
            _local_io(lambda: self._write_private(header_path, b""))
            _local_io(lambda: self._write_private(output_path, b""))
            _local_io(
                lambda: self._write_private(
                    config_path, ("\n".join(lines) + "\n").encode("utf-8")
                )
            )
            argv = [str(self.executable), "-q", "--config", str(config_path)]
            if self._cancel_check():
                raise _JiraTransportFailure(
                    "cancelled", outcome_uncertain=False
                )
            _remaining_control(control, outcome_uncertain=False)
            if deadline - self._clock() <= 0:
                raise _JiraTransportFailure(
                    "deadline", outcome_uncertain=False
                )
            try:
                process = self._popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                    cwd=temporary,
                )
            except (OSError, ValueError):
                raise _JiraTransportFailure(
                    "transient", outcome_uncertain=False
                ) from None
            communication_failure = None
            try:
                stdout, stderr = self._communicate(process, deadline, control)
            except _JiraTransportFailure as exc:
                communication_failure = (exc.category, exc.outcome_uncertain)
            except (OSError, ValueError):
                communication_failure = ("transient", True)
            finally:
                self._terminate(process)
            if communication_failure is not None:
                completed = self._completed_client_error(header_path)
                if completed is not None:
                    return completed
                category, outcome_uncertain = communication_failure
                raise _JiraTransportFailure(
                    category, outcome_uncertain=outcome_uncertain
                ) from None
            if len(stdout) > 16:
                raise _JiraTransportFailure("capacity", outcome_uncertain=True)
            status = (
                int(stdout)
                if re.fullmatch(rb"[1-5][0-9]{2}", stdout) is not None
                else None
            )
            deterministic_client_response = (
                status is not None and 400 <= status < 500
            )
            if len(stderr) > _MAX_STDERR_BYTES:
                if deterministic_client_response:
                    return TransportResponse(status=status, headers={}, body=b"")
                raise _JiraTransportFailure("capacity", outcome_uncertain=True)
            if process.returncode == 63:
                if deterministic_client_response:
                    return TransportResponse(status=status, headers={}, body=b"")
                raise _JiraTransportFailure("capacity", outcome_uncertain=True)
            if process.returncode == 28:
                if deterministic_client_response:
                    return TransportResponse(status=status, headers={}, body=b"")
                raise _JiraTransportFailure("deadline", outcome_uncertain=True)
            if process.returncode != 0:
                if deterministic_client_response:
                    return TransportResponse(status=status, headers={}, body=b"")
                raise _JiraTransportFailure("transient", outcome_uncertain=True)
            if status is None:
                raise _JiraTransportFailure(
                    "invalid_remote_data", outcome_uncertain=True
                )
            try:
                headers = self._parse_headers(
                    self._read_bounded(header_path, _MAX_HEADER_BYTES), status
                )
                body = self._read_bounded(output_path, _MAX_RESPONSE_BYTES)
            except _JiraTransportFailure:
                if not deterministic_client_response:
                    raise
                headers = {}
                body = b""
            return TransportResponse(status=status, headers=headers, body=body)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)


class NativeTransport:
    """Native HTTP, with an exact optional Cloudflare-1010 curl seam."""

    def __init__(
        self,
        authentication: JiraAuth,
        *,
        http_transport: httpx.BaseTransport | None = None,
        curl_transport=None,
        cancel_check: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.auth = authentication
        self._curl_transport = curl_transport
        self._cancel_check = cancel_check or (lambda: False)
        self._clock = clock
        if authentication.transport == "curl" and self._curl_transport is None:
            self._curl_transport = CurlTransport(
                authentication, cancel_check=cancel_check, clock=clock
            )
        self._client = httpx.Client(
            base_url=authentication.origin,
            headers={
                **authentication.headers,
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=authentication.request_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=http_transport,
        )

    def __repr__(self) -> str:
        return f"NativeTransport(origin={self.auth.origin!r})"

    def close(self) -> None:
        self._client.close()
        if self._curl_transport is not None:
            self._curl_transport.close()

    def _curl(self):
        if self._curl_transport is None:
            self._curl_transport = CurlTransport(
                self.auth,
                cancel_check=self._cancel_check,
                clock=self._clock,
            )
        return self._curl_transport

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        timeout_seconds: float,
    ) -> TransportResponse:
        return self.request_with_controls(
            method,
            path,
            params=params,
            json_body=json_body,
            timeout_seconds=timeout_seconds,
            control=None,
        )

    def request_with_controls(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        timeout_seconds: float,
        control: RequestControl | None,
    ) -> TransportResponse:
        _validate_method(method)
        _validate_path(path)
        if not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 120:
            raise JiraError("invalid_configuration")
        if self._cancel_check():
            raise _JiraTransportFailure("cancelled", outcome_uncertain=False)
        controlled_remaining = _remaining_control(
            control, outcome_uncertain=False
        )
        if controlled_remaining is not None:
            timeout_seconds = min(timeout_seconds, controlled_remaining)
        request_deadline = self._clock() + float(timeout_seconds)
        # Native requests must reject deterministic local encoding/capacity
        # failures before opening a socket, just like the curl transport.
        CurlTransport._build_url(self.auth.origin, path, params)
        request_body = CurlTransport._request_body(json_body)
        request_options = {
            "params": params,
            "json_body": json_body,
            "timeout_seconds": timeout_seconds,
        }
        if self.auth.transport == "curl":
            controlled_curl = getattr(self._curl(), "request_with_controls", None)
            if controlled_curl is not None:
                return controlled_curl(
                    method, path, control=control, **request_options
                )
            return self._curl().request(method, path, **request_options)
        invalid_request = False
        try:
            request = self._client.build_request(
                method,
                path,
                params=params,
                content=request_body,
                headers=(
                    {"Content-Type": "application/json"}
                    if request_body is not None
                    else None
                ),
                timeout=httpx.Timeout(timeout_seconds),
            )
        except (TypeError, ValueError, OverflowError, httpx.InvalidURL):
            invalid_request = True
        if invalid_request:
            raise _JiraTransportFailure(
                "invalid_input", outcome_uncertain=False
            ) from None
        if self._cancel_check():
            raise _JiraTransportFailure("cancelled", outcome_uncertain=False)
        final_control_remaining = _remaining_control(
            control, outcome_uncertain=False
        )
        remaining_before_dispatch = request_deadline - self._clock()
        if remaining_before_dispatch <= 0:
            raise _JiraTransportFailure("deadline", outcome_uncertain=False)
        if control is not None:
            timeout_seconds = min(
                timeout_seconds,
                remaining_before_dispatch,
                final_control_remaining,
            )
            request.extensions["timeout"] = {
                "connect": timeout_seconds,
                "read": timeout_seconds,
                "write": timeout_seconds,
                "pool": timeout_seconds,
            }
        failure = None
        known_status = None
        known_headers = {}
        try:
            with closing(self._client.send(request, stream=True)) as response:
                known_status = response.status_code
                known_headers = dict(response.headers)
                _remaining_control(control, outcome_uncertain=True)
                if self._cancel_check():
                    raise _JiraTransportFailure(
                        "cancelled", outcome_uncertain=True
                    )
                body = _read_native_body(
                    response,
                    control=control,
                    cancel_check=self._cancel_check,
                )
                result = TransportResponse(
                    status=known_status,
                    headers=known_headers,
                    body=body,
                )
        except _JiraTransportFailure:
            if known_status is None or not 400 <= known_status < 500:
                raise
            result = TransportResponse(
                status=known_status, headers=known_headers, body=b""
            )
        except httpx.RequestError:
            if known_status is not None and 400 <= known_status < 500:
                result = TransportResponse(
                    status=known_status, headers=known_headers, body=b""
                )
            else:
                failure = "transient"
        if failure is not None:
            raise _JiraTransportFailure(
                failure, outcome_uncertain=True
            ) from None
        if self.auth.transport == "auto" and _cloudflare_1010(result):
            controlled_curl = getattr(self._curl(), "request_with_controls", None)
            if controlled_curl is not None:
                return controlled_curl(
                    method, path, control=control, **request_options
                )
            return self._curl().request(method, path, **request_options)
        return result
