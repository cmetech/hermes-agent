"""Bounded native and private-curl HTTP transports for Jira."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlencode, urlsplit

import httpx

if __package__:
    from .models import JiraAuth, JiraError, TransportResponse
else:
    from models import JiraAuth, JiraError, TransportResponse


_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "DELETE"})
_MAX_REQUEST_BYTES = 256 * 1024
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_HEADER_BYTES = 64 * 1024
_MAX_STDERR_BYTES = 64 * 1024
_MAX_URL_BYTES = 8192
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
    parsed = urlsplit(path) if isinstance(path, str) else None
    if (
        parsed is None
        or not path.startswith("/rest/api/")
        or len(path.encode("utf-8")) > _MAX_URL_BYTES
        or parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or "\x00" in path
        or ".." in path.split("/")
    ):
        raise JiraError("invalid_input")


def _validate_method(method: str) -> None:
    if method not in _ALLOWED_METHODS:
        raise JiraError("invalid_input")


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
            raise JiraError("capacity")
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
            raise JiraError("capacity")
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

    def _communicate(self, process, deadline: float) -> tuple[bytes, bytes]:
        while True:
            if self._cancel_check():
                self._terminate(process)
                raise JiraError("cancelled")
            remaining = deadline - self._clock()
            if remaining <= 0:
                self._terminate(process)
                raise JiraError("deadline")
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
            raise JiraError("invalid_remote_data") from None
        if size > maximum:
            raise JiraError("capacity")
        try:
            return path.read_bytes()
        except OSError:
            raise JiraError("invalid_remote_data") from None

    @staticmethod
    def _parse_headers(raw: bytes, expected_status: int) -> dict[str, str]:
        if len(raw) > _MAX_HEADER_BYTES:
            raise JiraError("capacity")
        blocks = [block for block in raw.split(b"\r\n\r\n") if block]
        if not blocks:
            raise JiraError("invalid_remote_data")
        selected = blocks[-1]
        lines = selected.split(b"\r\n")
        try:
            status_line = lines[0].decode("ascii")
        except UnicodeDecodeError:
            raise JiraError("invalid_remote_data") from None
        match = re.fullmatch(r"HTTP/[12](?:\.0|\.1|) ([1-5][0-9]{2})(?: [^\r\n]{0,256})?", status_line)
        if match is None or int(match.group(1)) != expected_status:
            raise JiraError("invalid_remote_data")
        headers: dict[str, str] = {}
        for raw_line in lines[1:]:
            if not raw_line or raw_line[:1] in {b" ", b"\t"} or b":" not in raw_line:
                raise JiraError("invalid_remote_data")
            raw_name, raw_value = raw_line.split(b":", 1)
            try:
                name = raw_name.decode("ascii").lower()
                value = raw_value.decode("utf-8").strip()
            except UnicodeDecodeError:
                raise JiraError("invalid_remote_data") from None
            if (
                _HEADER_NAME.fullmatch(name) is None
                or len(value) > 4096
                or any(ord(character) < 32 and character != "\t" for character in value)
            ):
                raise JiraError("invalid_remote_data")
            if name in _SAFE_RESPONSE_HEADERS and name not in headers:
                headers[name] = value
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        timeout_seconds: float,
    ) -> TransportResponse:
        _validate_method(method)
        _validate_path(path)
        if not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 120:
            raise JiraError("invalid_configuration")
        if self._cancel_check():
            raise JiraError("cancelled")
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
        temporary = Path(tempfile.mkdtemp(prefix="ericsson-jira-curl-"))
        try:
            os.chmod(temporary, 0o700)
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
                self._write_private(body_path, request_body)
                lines.extend(
                    [
                        self._config_line("header", "Content-Type: application/json"),
                        self._config_line("data-binary", f"@{body_path}"),
                    ]
                )
            self._write_private(header_path, b"")
            self._write_private(output_path, b"")
            self._write_private(config_path, ("\n".join(lines) + "\n").encode("utf-8"))
            argv = [str(self.executable), "-q", "--config", str(config_path)]
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
                raise JiraError("transient") from None
            try:
                stdout, stderr = self._communicate(
                    process, deadline
                )
            finally:
                self._terminate(process)
            if len(stdout) > 16 or len(stderr) > _MAX_STDERR_BYTES:
                raise JiraError("capacity")
            if process.returncode == 63:
                raise JiraError("capacity")
            if process.returncode == 28:
                raise JiraError("deadline")
            if process.returncode != 0:
                raise JiraError("transient")
            if re.fullmatch(rb"[1-5][0-9]{2}", stdout) is None:
                raise JiraError("invalid_remote_data")
            status = int(stdout)
            headers = self._parse_headers(
                self._read_bounded(header_path, _MAX_HEADER_BYTES), status
            )
            body = self._read_bounded(output_path, _MAX_RESPONSE_BYTES)
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
    ) -> None:
        self.auth = authentication
        self._curl_transport = curl_transport
        self._cancel_check = cancel_check
        if authentication.transport == "curl" and self._curl_transport is None:
            self._curl_transport = CurlTransport(
                authentication, cancel_check=cancel_check
            )
        self._client = httpx.Client(
            base_url=authentication.origin,
            headers={**authentication.headers, "Accept": "application/json"},
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
                self.auth, cancel_check=self._cancel_check
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
        _validate_method(method)
        _validate_path(path)
        request_options = {
            "params": params,
            "json_body": json_body,
            "timeout_seconds": timeout_seconds,
        }
        if self.auth.transport == "curl":
            return self._curl().request(method, path, **request_options)
        response = self._client.request(
            method,
            path,
            params=params,
            json=json_body,
            timeout=httpx.Timeout(timeout_seconds),
        )
        result = TransportResponse(
            status=response.status_code,
            headers=dict(response.headers),
            body=response.content,
        )
        if self.auth.transport == "auto" and _cloudflare_1010(result):
            return self._curl().request(method, path, **request_options)
        return result
