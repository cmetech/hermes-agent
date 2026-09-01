"""Profile-scoped loopback transport for durable local Hermes Runs."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
from time import monotonic
import urllib.error
import urllib.parse
import urllib.request

from agent.secret_scope import build_profile_secret_scope
from gateway.config import Platform, load_gateway_config
from gateway.platforms.api_server import DEFAULT_HOST, DEFAULT_PORT
from hermes_cli.auth import has_usable_secret
from hermes_cli.profiles import get_profile_dir, profiles_to_serve, validate_profile_name
from hermes_cli.urllib_security import open_credentialed_url
from hermes_constants import (
    get_hermes_home,
    reset_hermes_home_override,
    set_hermes_home_override,
)
from tools.bot_relay import acquire_turn_lock, local_delivery_command
from tools.managed_process import ManagedProcessTree, ProcessIdentity

from .models import ChannelObservation, HandoffEndpoint, HandoffSnapshot
from .service import (
    ChannelDefinitelyNotAccepted,
    ChannelIndeterminate,
    ChannelRetryableFailure,
    EndpointAssessment,
)


MAX_RESPONSE_BYTES = 600_000
_READ_CHUNK_BYTES = 64 * 1024
_VALIDATE_BUDGET_SECONDS = 2.0
MAX_CLI_OUTPUT_BYTES = 500_000
_CLI_TIMEOUT_SECONDS = 600
_CLI_RECEIPT_VERSION = 1
_HANDOFF_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$")
_AUTHORITATIVE_RUNS_FAILURES = frozenset({
    "api_server_disabled",
    "api_server_key_missing",
    "api_server_key_weak",
    "listener_not_loopback",
    "multiplex_required",
    "profile_not_served",
    "runs_not_durable",
    "http_404",
    "http_405",
})
_INTERACTIVE_CAPABILITIES = frozenset({"approval", "interactive", "needs_input"})


@dataclass(frozen=True, slots=True)
class _Connection:
    base_url: str
    key: str


@dataclass(frozen=True, slots=True)
class _CLIPaths:
    root: Path
    prompt: Path
    stdout: Path
    stderr: Path
    receipt: Path


def _local_cli_failure(
    required_capabilities: frozenset[str], *, host_os: str
) -> str | None:
    if host_os == "nt":
        return "local_cli_lock_unavailable"
    if required_capabilities & _INTERACTIVE_CAPABILITIES:
        return "local_cli_capabilities_unavailable"
    return None


def _cli_paths(handoff_id: str) -> _CLIPaths:
    if not isinstance(handoff_id, str) or not _HANDOFF_ID.fullmatch(handoff_id):
        raise ValueError("handoff id is invalid")
    spool = get_hermes_home() / "handoffs"
    root = spool / handoff_id
    spool.mkdir(mode=0o700, parents=True, exist_ok=True)
    _verify_owner_dir(spool)
    root.mkdir(mode=0o700, exist_ok=True)
    _verify_owner_dir(root)
    return _CLIPaths(
        root=root,
        prompt=root / "prompt.txt",
        stdout=root / "stdout.txt",
        stderr=root / "stderr.txt",
        receipt=root / "receipt.json",
    )


def _verify_owner_dir(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError("handoff spool directory is unsafe")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ValueError("handoff spool directory has a different owner")
    os.chmod(path, 0o700)


def _verify_owner_file(path: Path, *, required: bool = False) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if required:
            raise ValueError("handoff spool file is missing") from None
        return
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("handoff spool file is unsafe")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ValueError("handoff spool file has a different owner")


def _atomic_bytes(path: Path, data: bytes) -> None:
    if len(data) > MAX_CLI_OUTPUT_BYTES:
        raise ValueError("handoff spool file exceeds byte limit")
    _verify_owner_file(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    _atomic_bytes(
        path,
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    )


def _read_bounded(path: Path) -> bytes:
    _verify_owner_file(path, required=True)
    with path.open("rb") as stream:
        data = stream.read(MAX_CLI_OUTPUT_BYTES + 1)
    if len(data) > MAX_CLI_OUTPUT_BYTES:
        raise ValueError("handoff spool file exceeds byte limit")
    return data


def _profile_root(home: Path) -> Path:
    return home.parent.parent if home.parent.name == "profiles" else home


def _wrapper_argv(handoff_id: str, profile: str) -> list[str]:
    return [
        sys.executable or "python3",
        "-m",
        "hermes_cli.handoff.local",
        "--run-cli",
        handoff_id,
        profile,
    ]


def _command_sha256(argv: list[str]) -> str:
    return sha256("\0".join(argv).encode("utf-8")).hexdigest()


def _run_cli_wrapper(handoff_id: str, profile: str) -> int:
    validate_profile_name(profile)
    paths = _cli_paths(handoff_id)
    _verify_owner_file(paths.prompt, required=True)
    for path in (paths.stdout, paths.stderr, paths.receipt):
        _verify_owner_file(path)
    prompt = _read_bounded(paths.prompt)
    request_sha256 = sha256(prompt).hexdigest()
    argv = local_delivery_command(
        profile,
        str(paths.prompt),
        title=f"Handoff: {handoff_id}",
    )
    try:
        with acquire_turn_lock(_profile_root(get_hermes_home()), profile):
            completed = subprocess.run(
                argv,
                capture_output=True,
                timeout=_CLI_TIMEOUT_SECONDS,
            )
        returncode = int(completed.returncode)
        exit_code = returncode if returncode >= 0 else 128 - returncode
        stdout = bytes(completed.stdout or b"")[:MAX_CLI_OUTPUT_BYTES]
        stderr = bytes(completed.stderr or b"")[:MAX_CLI_OUTPUT_BYTES]
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = bytes(exc.stdout or b"")[:MAX_CLI_OUTPUT_BYTES]
        stderr = (bytes(exc.stderr or b"") + b"\nlocal CLI timed out")[-MAX_CLI_OUTPUT_BYTES:]
    except Exception as exc:
        exit_code = 125
        stdout = b""
        stderr = f"local CLI wrapper failed: {type(exc).__name__}".encode("utf-8")
    _atomic_bytes(paths.stdout, stdout)
    _atomic_bytes(paths.stderr, stderr)
    _atomic_json(paths.receipt, {
        "version": _CLI_RECEIPT_VERSION,
        "handoff_id": handoff_id,
        "profile": profile,
        "request_sha256": request_sha256,
        "exit_code": exit_code,
        "stdout_size": len(stdout),
        "stderr_size": len(stderr),
        "stdout_sha256": sha256(stdout).hexdigest(),
        "stderr_sha256": sha256(stderr).hexdigest(),
    })
    return exit_code


class _Deadline:
    def __init__(self, seconds: float) -> None:
        self._at = monotonic() + seconds

    def remaining(self) -> float:
        remaining = self._at - monotonic()
        if remaining <= 0:
            raise TimeoutError("handoff operation budget expired")
        return remaining


def _listener_url(host: object, port: object) -> str | None:
    configured = str(host or DEFAULT_HOST).strip()
    lookup = (
        configured[1:-1]
        if configured.startswith("[") and configured.endswith("]")
        else configured
    )
    if lookup == "*":
        lookup = "127.0.0.1"
    else:
        try:
            if ipaddress.ip_address(lookup).is_unspecified:
                lookup = "127.0.0.1"
        except ValueError:
            pass
    try:
        addresses = {
            row[4][0].split("%", 1)[0]
            for row in socket.getaddrinfo(lookup, int(port), type=socket.SOCK_STREAM)
        }
        if not addresses or not all(
            ipaddress.ip_address(value).is_loopback for value in addresses
        ):
            return None
    except (OSError, TypeError, ValueError):
        return None
    rendered = f"[{lookup}]" if ":" in lookup else lookup
    return f"http://{rendered}:{int(port)}"


def _failure_code(exc: urllib.error.HTTPError) -> str:
    return f"http_{int(exc.code)}"


def _direct_local_opener(redirect_handler):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        redirect_handler,
    )
    opener.addheaders = []
    return opener


def _read_response(response, deadline: _Deadline) -> bytes:
    read = getattr(response, "read1", None) or response.read
    chunks = []
    total = 0
    while True:
        remaining = deadline.remaining()
        sock = getattr(
            getattr(getattr(response, "fp", None), "raw", None), "_sock", None
        )
        if sock is not None:
            try:
                sock.settimeout(remaining)
            except (OSError, ValueError):
                pass
        chunk = read(min(_READ_CHUNK_BYTES, MAX_RESPONSE_BYTES + 1 - total))
        deadline.remaining()
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise ValueError("handoff response exceeds byte limit")
        chunks.append(chunk)


def _request_json(
    connection: _Connection,
    path: str,
    deadline: _Deadline,
    *,
    method: str = "GET",
    body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    data = None
    if body is not None:
        data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    request_headers = {
        "Authorization": f"Bearer {connection.key}",
        "Content-Type": "application/json",
        "User-Agent": "hermes-local-handoff",
    }
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        f"{connection.base_url}{path}",
        data=data,
        method=method,
        headers=request_headers,
    )
    with open_credentialed_url(
        request,
        timeout=deadline.remaining(),
        opener_factory=_direct_local_opener,
    ) as response:
        raw = _read_response(response, deadline)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("handoff endpoint returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("handoff endpoint returned non-object JSON")
    return value


def _checkpoint(snapshot: HandoffSnapshot, **changes: object) -> dict[str, object]:
    values = dict(snapshot.checkpoint or {})
    values.update(changes)
    return values


class LocalHermesChannel:
    """Duck-typed handoff channel backed by the local multiplex API server."""

    def _connection(self, profile: str) -> tuple[_Connection | None, str | None]:
        default_home = get_profile_dir("default")
        token = set_hermes_home_override(default_home)
        try:
            config = load_gateway_config()
        finally:
            reset_hermes_home_override(token)

        if not config.multiplex_profiles:
            return None, "multiplex_required"
        api_config = config.platforms.get(Platform.API_SERVER)
        if api_config is None or not api_config.enabled:
            return None, "api_server_disabled"

        served = dict(
            profiles_to_serve(
                multiplex=True,
                profile_allowlist=config.multiplex_profile_allowlist,
            )
        )
        target_home = served.get(profile)
        if target_home is None:
            return None, "profile_not_served"

        key = (
            build_profile_secret_scope(target_home).get("API_SERVER_KEY") or ""
        ).strip()
        if not key:
            return None, "api_server_key_missing"
        if not has_usable_secret(key, min_length=16):
            return None, "api_server_key_weak"

        extra = api_config.extra or {}
        base = _listener_url(
            extra.get("host", DEFAULT_HOST), extra.get("port", DEFAULT_PORT)
        )
        if base is None:
            return None, "listener_not_loopback"
        prefix = urllib.parse.quote(profile, safe="")
        return _Connection(f"{base}/p/{prefix}", key), None

    @staticmethod
    def _durability_failure(capabilities: dict[str, object]) -> str | None:
        features = capabilities.get("features")
        contract = (
            features.get("runs_idempotency") if isinstance(features, dict) else None
        )
        if (
            not isinstance(contract, dict)
            or contract.get("supported") is not True
            or contract.get("durable") is not True
        ):
            return "runs_not_durable"
        return None

    def _assess(
        self, endpoint: HandoffEndpoint, deadline: _Deadline
    ) -> tuple[_Connection | None, str | None]:
        connection, failure = self._connection(endpoint.profile)
        if connection is None:
            return None, failure
        try:
            capabilities = _request_json(connection, "/v1/capabilities", deadline)
        except urllib.error.HTTPError as exc:
            return None, _failure_code(exc)
        except (OSError, TimeoutError, ValueError):
            return None, "endpoint_unavailable"
        return (
            (connection, None)
            if (failure := self._durability_failure(capabilities)) is None
            else (None, failure)
        )

    def validate_endpoint(
        self, endpoint: HandoffEndpoint, _initiator: str
    ) -> EndpointAssessment:
        _connection, failure = self._assess(
            endpoint, _Deadline(_VALIDATE_BUDGET_SECONDS)
        )
        if failure in _AUTHORITATIVE_RUNS_FAILURES:
            cli_failure = _local_cli_failure(frozenset(), host_os=os.name)
            if cli_failure is None:
                return EndpointAssessment(
                    endpoint=endpoint, available=True, mechanism="local_cli"
                )
            failure = cli_failure
        return EndpointAssessment(
            endpoint=endpoint,
            available=failure is None,
            mechanism="runs" if failure is None else None,
            failure_code=failure,
        )

    def _find_session(
        self, connection: _Connection, title: str, deadline: _Deadline
    ) -> str | None:
        query = urllib.parse.urlencode({
            "limit": 200,
            "title": title,
            "include_hidden": 1,
        })
        listing = _request_json(connection, f"/api/sessions?{query}", deadline)
        rows = listing.get("data")
        if not isinstance(rows, list):
            raise ValueError("handoff session listing is invalid")
        for row in rows:
            if isinstance(row, dict) and (row.get("title") or "").strip() == title:
                session_id = str(row.get("id") or "")
                return session_id or None
        return None

    def _ensure_session(
        self, connection: _Connection, handoff_id: str, deadline: _Deadline
    ) -> str:
        title = f"Handoff: {handoff_id}"
        existing = self._find_session(connection, title, deadline)
        if existing:
            return existing
        try:
            created = _request_json(
                connection,
                "/api/sessions",
                deadline,
                method="POST",
                body={"title": title, "source": "handoff"},
            )
        except urllib.error.HTTPError as exc:
            if exc.code != 400:
                raise
            existing = self._find_session(connection, title, deadline)
            if existing:
                return existing
            raise
        session = created.get("session")
        session_id = str(session.get("id") or "") if isinstance(session, dict) else ""
        if not session_id:
            raise ValueError("handoff session creation returned no id")
        return session_id

    def bind(
        self, snapshot: HandoffSnapshot, *, budget_seconds: float
    ) -> ChannelObservation:
        deadline = _Deadline(budget_seconds)
        connection, failure = self._assess(snapshot.spec.endpoint, deadline)
        if connection is None:
            if failure in _AUTHORITATIVE_RUNS_FAILURES:
                cli_failure = _local_cli_failure(
                    snapshot.spec.required_capabilities, host_os=os.name
                )
                if cli_failure is not None:
                    return ChannelObservation(
                        phase="failed", failure_code=cli_failure
                    )
                return self._bind_cli(snapshot)
            return ChannelObservation(phase="prepared", failure_code=failure)
        try:
            session_id = self._ensure_session(connection, snapshot.handoff_id, deadline)
        except urllib.error.HTTPError as exc:
            return ChannelObservation(phase="prepared", failure_code=_failure_code(exc))
        except (OSError, TimeoutError, ValueError):
            return ChannelObservation(
                phase="prepared", failure_code="endpoint_unavailable"
            )
        return ChannelObservation(
            phase="prepared",
            mechanism="runs",
            binding={"profile": snapshot.spec.endpoint.profile, "mechanism": "runs"},
            checkpoint={"session_id": session_id},
        )

    @staticmethod
    def _bind_cli(snapshot: HandoffSnapshot) -> ChannelObservation:
        paths = _cli_paths(snapshot.handoff_id)
        prompt = snapshot.spec.prompt.encode("utf-8")
        _atomic_bytes(paths.prompt, prompt)
        return ChannelObservation(
            phase="prepared",
            mechanism="local_cli",
            binding={
                "profile": snapshot.spec.endpoint.profile,
                "mechanism": "local_cli",
            },
            checkpoint={"request_sha256": sha256(prompt).hexdigest()},
        )

    @staticmethod
    def _process_identity(snapshot: HandoffSnapshot) -> ProcessIdentity | None:
        checkpoint = snapshot.checkpoint or {}
        pid = checkpoint.get("process_pid")
        started_at = checkpoint.get("process_started_at")
        if (
            isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid <= 0
            or isinstance(started_at, bool)
            or not isinstance(started_at, int)
            or started_at < 0
        ):
            return None
        return ProcessIdentity(pid=pid, start_time=started_at, group_id=pid)

    @staticmethod
    def _identity_is_current(identity: ProcessIdentity) -> bool:
        if not identity.is_current():
            return False
        current = ProcessIdentity.capture(identity.pid)
        return (
            current.pid == identity.pid
            and current.start_time == identity.start_time
            and current.group_id == identity.group_id
        )

    @staticmethod
    def _receipt_observation(snapshot: HandoffSnapshot) -> ChannelObservation | None:
        paths = _cli_paths(snapshot.handoff_id)
        try:
            raw_receipt = _read_bounded(paths.receipt)
            receipt = json.loads(raw_receipt)
            if not isinstance(receipt, dict) or set(receipt) != {
                "version", "handoff_id", "profile", "request_sha256", "exit_code",
                "stdout_size", "stderr_size", "stdout_sha256", "stderr_sha256",
            }:
                return None
            stdout = _read_bounded(paths.stdout)
            stderr = _read_bounded(paths.stderr)
            if (
                receipt["version"] != _CLI_RECEIPT_VERSION
                or receipt["handoff_id"] != snapshot.handoff_id
                or receipt["profile"] != snapshot.spec.endpoint.profile
                or receipt["request_sha256"]
                != (snapshot.checkpoint or {}).get("request_sha256")
                or receipt["stdout_size"] != len(stdout)
                or receipt["stderr_size"] != len(stderr)
                or receipt["stdout_sha256"] != sha256(stdout).hexdigest()
                or receipt["stderr_sha256"] != sha256(stderr).hexdigest()
                or isinstance(receipt["exit_code"], bool)
                or not isinstance(receipt["exit_code"], int)
                or receipt["exit_code"] < 0
            ):
                return None
            text = stdout.decode("utf-8")
        except (OSError, UnicodeDecodeError, ValueError, TypeError):
            return None
        checkpoint = _checkpoint(
            snapshot,
            receipt_version=_CLI_RECEIPT_VERSION,
            exit_code=receipt["exit_code"],
            receipt_sha256=sha256(raw_receipt).hexdigest(),
            stdout_sha256=receipt["stdout_sha256"],
            stderr_sha256=receipt["stderr_sha256"],
            output_sha256=sha256(text.encode("utf-8")).hexdigest(),
            status="completed" if receipt["exit_code"] == 0 else "failed",
        )
        if receipt["exit_code"] != 0:
            return ChannelObservation(
                phase="failed",
                checkpoint=checkpoint,
                failure_code="local_cli_failed",
            )
        return ChannelObservation(
            phase="succeeded",
            checkpoint=checkpoint,
            terminal_result=LocalHermesChannel._terminal_result(text),
        )

    def _submit_cli(self, snapshot: HandoffSnapshot) -> ChannelObservation:
        paths = _cli_paths(snapshot.handoff_id)
        prompt = _read_bounded(paths.prompt)
        if sha256(prompt).hexdigest() != (snapshot.checkpoint or {}).get(
            "request_sha256"
        ):
            raise ChannelDefinitelyNotAccepted("local_cli_prompt_changed")
        argv = _wrapper_argv(
            snapshot.handoff_id, snapshot.spec.endpoint.profile
        )
        tree = ManagedProcessTree.spawn(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        identity = tree.identity
        if identity.start_time is None or identity.group_id != identity.pid:
            ManagedProcessTree.terminate_existing(identity)
            raise ChannelIndeterminate("local_cli_identity_unavailable")
        return ChannelObservation(
            phase="submitted",
            checkpoint=_checkpoint(
                snapshot,
                process_pid=identity.pid,
                process_started_at=identity.start_time,
                process_command_sha256=_command_sha256(argv),
                status="running",
            ),
        )

    def _observe_cli(
        self, snapshot: HandoffSnapshot, *, cancelling: bool = False
    ) -> ChannelObservation:
        receipt = self._receipt_observation(snapshot)
        if receipt is not None:
            return receipt
        identity = self._process_identity(snapshot)
        if identity is not None and self._identity_is_current(identity):
            return ChannelObservation(
                phase="cancelling" if cancelling else "active",
                checkpoint=_checkpoint(snapshot, status="running"),
            )
        return ChannelObservation(
            phase="indeterminate",
            checkpoint=snapshot.checkpoint or {},
            failure_code="local_cli_process_lost",
        )

    def _bound_connection(self, snapshot: HandoffSnapshot) -> _Connection:
        profile = str((snapshot.binding or {}).get("profile") or "")
        connection, failure = self._connection(profile)
        if connection is None:
            raise ChannelIndeterminate(failure or "endpoint_unavailable")
        return connection

    def _submit(
        self, snapshot: HandoffSnapshot, deadline: _Deadline
    ) -> ChannelObservation:
        connection = self._bound_connection(snapshot)
        session_id = str((snapshot.checkpoint or {}).get("session_id") or "")
        if not session_id:
            raise ChannelIndeterminate("session_missing")
        idempotency_key = f"handoff-{snapshot.handoff_id}"
        body = {"input": snapshot.spec.prompt, "session_id": session_id}
        try:
            response = _request_json(
                connection,
                "/v1/runs",
                deadline,
                method="POST",
                body=body,
                headers={"Idempotency-Key": idempotency_key},
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                raise ChannelDefinitelyNotAccepted("idempotency_conflict") from exc
            if 400 <= exc.code < 500:
                raise ChannelDefinitelyNotAccepted(_failure_code(exc)) from exc
            raise ChannelIndeterminate() from exc
        except (OSError, TimeoutError, ValueError) as exc:
            raise ChannelIndeterminate() from exc
        run_id = str(response.get("run_id") or "")
        if not run_id:
            raise ChannelIndeterminate()
        return ChannelObservation(
            phase="submitted",
            checkpoint=_checkpoint(
                snapshot,
                session_id=session_id,
                run_id=run_id,
                idempotency_key=idempotency_key,
                status=str(response.get("status") or "queued"),
            ),
        )

    def submit(
        self, snapshot: HandoffSnapshot, *, budget_seconds: float
    ) -> ChannelObservation:
        if snapshot.mechanism == "local_cli":
            return self._submit_cli(snapshot)
        return self._submit(snapshot, _Deadline(budget_seconds))

    @staticmethod
    def _terminal_result(output: object) -> dict[str, object]:
        text = (
            output
            if isinstance(output, str)
            else json.dumps(output, sort_keys=True, separators=(",", ":"))
        )
        encoded = text.encode("utf-8")
        return {
            "text": text,
            "sha256": sha256(encoded).hexdigest(),
            "media_type": "text/plain"
            if isinstance(output, str)
            else "application/json",
            "size_bytes": len(encoded),
        }

    def _observe(
        self,
        snapshot: HandoffSnapshot,
        deadline: _Deadline,
        *,
        cancelling: bool = False,
    ) -> ChannelObservation:
        connection = self._bound_connection(snapshot)
        run_id = str((snapshot.checkpoint or {}).get("run_id") or "")
        if not run_id:
            raise ChannelIndeterminate()
        try:
            response = _request_json(
                connection,
                f"/v1/runs/{urllib.parse.quote(run_id, safe='')}",
                deadline,
            )
        except urllib.error.HTTPError as exc:
            if exc.code >= 500:
                raise ChannelRetryableFailure() from exc
            raise ChannelIndeterminate() from exc
        except (OSError, TimeoutError, ValueError) as exc:
            raise ChannelRetryableFailure() from exc

        status = str(response.get("status") or "unknown")
        checkpoint = _checkpoint(snapshot, run_id=run_id, status=status)
        if status == "completed":
            try:
                result = self._terminal_result(response.get("output", ""))
                return ChannelObservation(
                    phase="succeeded", checkpoint=checkpoint, terminal_result=result
                )
            except (TypeError, UnicodeError, ValueError) as exc:
                raise ChannelIndeterminate() from exc
        if status == "failed":
            return ChannelObservation(
                phase="failed", checkpoint=checkpoint, failure_code="remote_failed"
            )
        if status == "cancelled":
            return ChannelObservation(phase="cancelled", checkpoint=checkpoint)
        if status == "interrupted":
            return ChannelObservation(
                phase="indeterminate",
                checkpoint=checkpoint,
                failure_code="run_interrupted",
            )
        if cancelling or status == "stopping":
            return ChannelObservation(phase="cancelling", checkpoint=checkpoint)
        phase = {
            "queued": "submitted",
            "started": "submitted",
            "running": "active",
            "waiting_for_approval": "needs_input",
        }.get(status)
        if phase is None:
            return ChannelObservation(
                phase="indeterminate",
                checkpoint=checkpoint,
                failure_code="run_status_unknown",
            )
        return ChannelObservation(phase=phase, checkpoint=checkpoint)

    def reconcile(
        self, snapshot: HandoffSnapshot, *, budget_seconds: float
    ) -> ChannelObservation:
        if snapshot.mechanism == "local_cli":
            return self._observe_cli(
                snapshot, cancelling=snapshot.cancel_requested_at is not None
            )
        deadline = _Deadline(budget_seconds)
        if (snapshot.checkpoint or {}).get("run_id"):
            return self._observe(
                snapshot,
                deadline,
                cancelling=snapshot.cancel_requested_at is not None,
            )
        return self._submit(snapshot, deadline)

    def observe(
        self, snapshot: HandoffSnapshot, *, budget_seconds: float
    ) -> ChannelObservation:
        if snapshot.mechanism == "local_cli":
            return self._observe_cli(
                snapshot, cancelling=snapshot.cancel_requested_at is not None
            )
        return self._observe(snapshot, _Deadline(budget_seconds))

    def cancel(
        self, snapshot: HandoffSnapshot, *, budget_seconds: float
    ) -> ChannelObservation:
        if snapshot.mechanism == "local_cli":
            receipt = self._receipt_observation(snapshot)
            if receipt is not None:
                return receipt
            identity = self._process_identity(snapshot)
            if identity is None:
                return ChannelObservation(
                    phase="indeterminate",
                    checkpoint=snapshot.checkpoint or {},
                    failure_code="cancellation_indeterminate",
                )
            if not self._identity_is_current(identity):
                return ChannelObservation(
                    phase="indeterminate",
                    checkpoint=snapshot.checkpoint or {},
                    failure_code="cancellation_indeterminate",
                )
            if ManagedProcessTree.terminate_existing(identity):
                receipt = self._receipt_observation(snapshot)
                return receipt or ChannelObservation(
                    phase="cancelled",
                    checkpoint=_checkpoint(snapshot, status="stopped"),
                )
            return ChannelObservation(
                phase="cancelling",
                checkpoint=_checkpoint(snapshot, status="stopping"),
            )
        deadline = _Deadline(budget_seconds)
        if (snapshot.checkpoint or {}).get("status") == "stopping":
            return self._observe(snapshot, deadline, cancelling=True)
        connection = self._bound_connection(snapshot)
        run_id = str((snapshot.checkpoint or {}).get("run_id") or "")
        if not run_id:
            raise ChannelIndeterminate()
        try:
            _request_json(
                connection,
                f"/v1/runs/{urllib.parse.quote(run_id, safe='')}/stop",
                deadline,
                method="POST",
                body={},
            )
        except urllib.error.HTTPError as exc:
            raise ChannelIndeterminate() from exc
        except (OSError, TimeoutError, ValueError) as exc:
            raise ChannelIndeterminate() from exc
        return ChannelObservation(
            phase="cancelling",
            checkpoint=_checkpoint(snapshot, run_id=run_id, status="stopping"),
        )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--run-cli", action="store_true")
    parser.add_argument("handoff_id")
    parser.add_argument("profile")
    args = parser.parse_args(argv)
    if not args.run_cli or os.name == "nt":
        return 2
    return _run_cli_wrapper(args.handoff_id, args.profile)


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = ["LocalHermesChannel", "MAX_CLI_OUTPUT_BYTES", "MAX_RESPONSE_BYTES"]
