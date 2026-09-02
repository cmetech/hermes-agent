"""Profile-scoped loopback transport for durable local Hermes Runs."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import subprocess
import sys
import threading
import urllib.error
import urllib.parse

from agent.deadline import kill_process_tree
from agent.secret_scope import build_profile_secret_scope
from gateway.config import Platform, load_gateway_config
from gateway.platforms.api_server import DEFAULT_HOST, DEFAULT_PORT
from hermes_cli.auth import has_usable_secret
from hermes_cli.profiles import get_profile_dir, profiles_to_serve, validate_profile_name
from hermes_constants import (
    get_hermes_home,
    reset_hermes_home_override,
    set_hermes_home_override,
)
from tools.bot_relay import acquire_turn_lock, local_delivery_command
from tools.environments.local import hermes_subprocess_env
from tools.managed_process import ManagedProcessTree, ProcessIdentity

from .models import ChannelObservation, HandoffEndpoint, HandoffSnapshot
from .runs import (
    MAX_RESPONSE_BYTES,
    RunsClient,
    RunsConnection,
    RunsDeadline,
    observation_from_status,
    terminal_result,
)
from .service import (
    ChannelDefinitelyNotAccepted,
    ChannelIndeterminate,
    ChannelRetryableFailure,
    EndpointAssessment,
)


_READ_CHUNK_BYTES = 64 * 1024
_VALIDATE_BUDGET_SECONDS = 2.0
MAX_CLI_OUTPUT_BYTES = 500_000
_CLI_TIMEOUT_SECONDS = 600
_CLI_RECEIPT_VERSION = 3
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


def _cli_paths(source_home: Path, handoff_id: str) -> _CLIPaths:
    with _open_cli_dir(source_home, handoff_id) as (paths, _directory_fd):
        return paths


def _path_flags(*, directory: bool = False) -> int:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    return flags


def _verify_owner_dir_fd(fd: int) -> None:
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("handoff spool directory is unsafe")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ValueError("handoff spool directory has a different owner")
    os.fchmod(fd, 0o700)


@contextmanager
def _open_cli_dir(source_home: Path, handoff_id: str):
    if not isinstance(handoff_id, str) or not _HANDOFF_ID.fullmatch(handoff_id):
        raise ValueError("handoff id is invalid")
    spool = source_home / "handoffs"
    root = spool / handoff_id
    spool.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        spool_fd = os.open(spool, _path_flags(directory=True))
    except OSError as exc:
        raise ValueError("handoff spool directory is unsafe") from exc
    try:
        _verify_owner_dir_fd(spool_fd)
        try:
            os.mkdir(handoff_id, 0o700, dir_fd=spool_fd)
        except FileExistsError:
            pass
        try:
            directory_fd = os.open(
                handoff_id, _path_flags(directory=True), dir_fd=spool_fd
            )
        except OSError as exc:
            raise ValueError("handoff spool directory is unsafe") from exc
        try:
            _verify_owner_dir_fd(directory_fd)
            yield (
                _CLIPaths(
                    root=root,
                    prompt=root / "prompt.txt",
                    stdout=root / "stdout.txt",
                    stderr=root / "stderr.txt",
                    receipt=root / "receipt.json",
                ),
                directory_fd,
            )
        finally:
            os.close(directory_fd)
    finally:
        os.close(spool_fd)


def _remove_cli_spool(source_home: Path, handoff_id: str) -> None:
    if not isinstance(handoff_id, str) or not _HANDOFF_ID.fullmatch(handoff_id):
        raise ValueError("handoff id is invalid")
    spool = source_home / "handoffs"
    try:
        spool_fd = os.open(spool, _path_flags(directory=True))
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ValueError("handoff spool directory is unsafe") from exc
    try:
        _verify_owner_dir_fd(spool_fd)
        try:
            directory_fd = os.open(
                handoff_id, _path_flags(directory=True), dir_fd=spool_fd
            )
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ValueError("handoff spool directory is unsafe") from exc
        try:
            _verify_owner_dir_fd(directory_fd)
            for name in ("prompt.txt", "stdout.txt", "stderr.txt", "receipt.json"):
                _verify_owner_file_at(directory_fd, name)
                try:
                    os.unlink(name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        try:
            os.rmdir(handoff_id, dir_fd=spool_fd)
        except FileNotFoundError:
            pass
        os.fsync(spool_fd)
    finally:
        os.close(spool_fd)


def _verify_owner_file_fd(fd: int) -> None:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("handoff spool file is unsafe")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ValueError("handoff spool file has a different owner")


def _open_owner_file_at(directory_fd: int, name: str, *, required: bool) -> int | None:
    try:
        fd = os.open(name, _path_flags(), dir_fd=directory_fd)
    except FileNotFoundError:
        if required:
            raise ValueError("handoff spool file is missing") from None
        return None
    except OSError as exc:
        raise ValueError("handoff spool file is unsafe") from exc
    try:
        _verify_owner_file_fd(fd)
        return fd
    except Exception:
        os.close(fd)
        raise


def _verify_owner_file_at(directory_fd: int, name: str, *, required: bool = False) -> None:
    fd = _open_owner_file_at(directory_fd, name, required=required)
    if fd is not None:
        os.close(fd)


def _atomic_bytes_at(directory_fd: int, name: str, data: bytes) -> None:
    if len(data) > MAX_CLI_OUTPUT_BYTES:
        raise ValueError("handoff spool file exceeds byte limit")
    _verify_owner_file_at(directory_fd, name)
    temporary = f".{name}-{secrets.token_hex(8)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(temporary, flags, 0o600, dir_fd=directory_fd)
    except OSError as exc:
        raise ValueError("handoff spool temporary file is unsafe") from exc
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
        _verify_owner_file_fd(fd)
        os.replace(
            temporary,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    except Exception:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except OSError:
            pass
        raise
    finally:
        os.close(fd)


def _atomic_bytes(path: Path, data: bytes) -> None:
    try:
        directory_fd = os.open(path.parent, _path_flags(directory=True))
    except OSError as exc:
        raise ValueError("handoff spool directory is unsafe") from exc
    try:
        _verify_owner_dir_fd(directory_fd)
        _atomic_bytes_at(directory_fd, path.name, data)
    finally:
        os.close(directory_fd)


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    _atomic_bytes(
        path,
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    )


def _atomic_json_at(
    directory_fd: int, name: str, value: dict[str, object]
) -> None:
    _atomic_bytes_at(
        directory_fd,
        name,
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    )


def _read_bounded(path: Path) -> bytes:
    try:
        directory_fd = os.open(path.parent, _path_flags(directory=True))
    except OSError as exc:
        raise ValueError("handoff spool directory is unsafe") from exc
    try:
        _verify_owner_dir_fd(directory_fd)
        return _read_bounded_at(directory_fd, path.name)
    finally:
        os.close(directory_fd)


def _read_bounded_fd(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    data = os.read(fd, MAX_CLI_OUTPUT_BYTES + 1)
    if len(data) > MAX_CLI_OUTPUT_BYTES:
        raise ValueError("handoff spool file exceeds byte limit")
    return data


def _read_bounded_at(directory_fd: int, name: str) -> bytes:
    fd = _open_owner_file_at(directory_fd, name, required=True)
    assert fd is not None
    try:
        return _read_bounded_fd(fd)
    finally:
        os.close(fd)


class _BoundedOutput:
    def __init__(self) -> None:
        self._data = bytearray()
        self._lock = threading.Lock()

    def append(self, chunk: bytes) -> None:
        with self._lock:
            remaining = MAX_CLI_OUTPUT_BYTES - len(self._data)
            if remaining > 0:
                self._data.extend(chunk[:remaining])

    def snapshot(self) -> bytes:
        with self._lock:
            return bytes(self._data)


def _drain_pipe(stream, output: _BoundedOutput) -> None:
    try:
        while chunk := stream.read(_READ_CHUNK_BYTES):
            output.append(bytes(chunk))
    except (OSError, ValueError):
        pass


def _collect_process_output(
    process,
    *,
    timeout_seconds: float,
) -> tuple[int, bytes, bytes, bool]:
    stdout = _BoundedOutput()
    stderr = _BoundedOutput()
    readers = tuple(
        threading.Thread(target=_drain_pipe, args=(stream, output), daemon=True)
        for stream, output in (
            (process.stdout, stdout),
            (process.stderr, stderr),
        )
    )
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
    if timed_out:
        pid = getattr(process, "pid", None)
        terminated = (
            kill_process_tree(pid)
            if isinstance(pid, int) and not isinstance(pid, bool)
            else False
        )
        if not terminated:
            try:
                process.kill()
            except (AttributeError, OSError, ProcessLookupError, PermissionError):
                pass
        try:
            process.wait(timeout=1)
        except (subprocess.TimeoutExpired, OSError):
            pass
    for reader in readers:
        reader.join(timeout=1)
    returncode = process.returncode
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        returncode = 124 if timed_out else 125
    return returncode, stdout.snapshot(), stderr.snapshot(), timed_out


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
    source_home = get_hermes_home().expanduser().resolve()
    with _open_cli_dir(source_home, handoff_id) as (_paths, directory_fd):
        for name in ("stdout.txt", "stderr.txt", "receipt.json"):
            _verify_owner_file_at(directory_fd, name)
        try:
            os.unlink("stderr.txt", dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        prompt_fd = _open_owner_file_at(directory_fd, "prompt.txt", required=True)
        assert prompt_fd is not None
        try:
            prompt = _read_bounded_fd(prompt_fd)
            request_sha256 = sha256(prompt).hexdigest()
            os.lseek(prompt_fd, 0, os.SEEK_SET)
            os.unlink("prompt.txt", dir_fd=directory_fd)
            os.fsync(directory_fd)
            argv = local_delivery_command(
                profile,
                f"/dev/fd/{prompt_fd}",
                title=f"Handoff: {handoff_id}",
            )
            try:
                with acquire_turn_lock(_profile_root(get_hermes_home()), profile):
                    process = subprocess.Popen(
                        argv,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        pass_fds=(prompt_fd,),
                    )
                    returncode, stdout, stderr, timed_out = _collect_process_output(
                        process,
                        timeout_seconds=_CLI_TIMEOUT_SECONDS,
                    )
                if timed_out:
                    outcome = "timeout"
                    exit_code = 124
                    stderr = (stderr + b"\nlocal CLI timed out")[-MAX_CLI_OUTPUT_BYTES:]
                else:
                    outcome = "completed"
                    exit_code = returncode if returncode >= 0 else 128 - returncode
            except Exception as exc:
                outcome = "wrapper_error"
                exit_code = 125
                stdout = b""
                stderr = (
                    f"local CLI wrapper failed: {type(exc).__name__}".encode("utf-8")
                )
        finally:
            os.close(prompt_fd)
        _atomic_bytes_at(directory_fd, "stdout.txt", stdout)
        _atomic_json_at(directory_fd, "receipt.json", {
            "version": _CLI_RECEIPT_VERSION,
            "handoff_id": handoff_id,
            "profile": profile,
            "request_sha256": request_sha256,
            "outcome": outcome,
            "exit_code": exit_code,
            "stdout_size": len(stdout),
            "stderr_size": len(stderr),
            "stdout_sha256": sha256(stdout).hexdigest(),
            "stderr_sha256": sha256(stderr).hexdigest(),
        })
        return exit_code


_Connection = RunsConnection
_Deadline = RunsDeadline


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


def _checkpoint(snapshot: HandoffSnapshot, **changes: object) -> dict[str, object]:
    values = dict(snapshot.checkpoint or {})
    values.update(changes)
    return values


class LocalHermesChannel:
    """Duck-typed handoff channel backed by the local multiplex API server."""

    def __init__(self, source_home: Path | str | None = None) -> None:
        self._source_home = Path(
            source_home if source_home is not None else get_hermes_home()
        ).expanduser().resolve()
        self._cli_trees: dict[str, ManagedProcessTree] = {}
        self._cli_trees_lock = threading.Lock()

    def _take_cli_tree(self, handoff_id: str) -> ManagedProcessTree | None:
        with self._cli_trees_lock:
            return self._cli_trees.pop(handoff_id, None)

    def cleanup_committed(self, snapshot: HandoffSnapshot) -> None:
        """Remove transient CLI bytes after their durable fact is committed."""
        if snapshot.mechanism != "local_cli":
            return
        checkpoint = snapshot.checkpoint or {}
        if (
            snapshot.phase not in {"succeeded", "failed", "cancelled", "indeterminate"}
            and "receipt_sha256" not in checkpoint
        ):
            return
        if snapshot.phase == "indeterminate" and "receipt_sha256" not in checkpoint:
            identity = self._process_identity(snapshot)
            if identity is not None and not self._identity_is_gone(identity):
                return
        tree = self._take_cli_tree(snapshot.handoff_id)
        if tree is not None:
            tree.terminate("handoff observation committed")
        _remove_cli_spool(self._source_home, snapshot.handoff_id)

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
            capabilities = RunsClient(connection, deadline).request_json(
                "/v1/capabilities"
            )
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
        listing = RunsClient(connection, deadline).request_json(
            f"/api/sessions?{query}"
        )
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
            created = RunsClient(connection, deadline).request_json(
                "/api/sessions",
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

    def _bind_cli(self, snapshot: HandoffSnapshot) -> ChannelObservation:
        prompt = snapshot.spec.prompt.encode("utf-8")
        with _open_cli_dir(
            self._source_home, snapshot.handoff_id
        ) as (_paths, directory_fd):
            _atomic_bytes_at(directory_fd, "prompt.txt", prompt)
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
    def _identity_is_gone(identity: ProcessIdentity) -> bool:
        if identity.start_time is None or identity.group_id is None:
            return False
        try:
            from gateway.status import _pid_exists

            if _pid_exists(identity.pid):
                current = ProcessIdentity.capture(identity.pid)
                if (
                    current.start_time == identity.start_time
                    and current.group_id == identity.group_id
                ):
                    return False
        except Exception:
            return False
        try:
            os.killpg(identity.group_id, 0)
        except ProcessLookupError:
            return True
        except (OSError, PermissionError):
            return False
        return False

    def _receipt_observation(
        self, snapshot: HandoffSnapshot
    ) -> ChannelObservation | None:
        try:
            with _open_cli_dir(
                self._source_home, snapshot.handoff_id
            ) as (_paths, directory_fd):
                raw_receipt = _read_bounded_at(directory_fd, "receipt.json")
                receipt = json.loads(raw_receipt)
                if not isinstance(receipt, dict) or set(receipt) != {
                    "version", "handoff_id", "profile", "request_sha256",
                    "outcome", "exit_code", "stdout_size", "stderr_size",
                    "stdout_sha256", "stderr_sha256",
                }:
                    return None
                stdout = _read_bounded_at(directory_fd, "stdout.txt")
            if (
                receipt["version"] != _CLI_RECEIPT_VERSION
                or receipt["handoff_id"] != snapshot.handoff_id
                or receipt["profile"] != snapshot.spec.endpoint.profile
                or receipt["request_sha256"]
                != (snapshot.checkpoint or {}).get("request_sha256")
                or receipt["stdout_size"] != len(stdout)
                or receipt["stdout_sha256"] != sha256(stdout).hexdigest()
                or isinstance(receipt["stderr_size"], bool)
                or not isinstance(receipt["stderr_size"], int)
                or not 0 <= receipt["stderr_size"] <= MAX_CLI_OUTPUT_BYTES
                or not isinstance(receipt["stderr_sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", receipt["stderr_sha256"])
                is None
                or receipt["outcome"] not in {
                    "completed", "timeout", "wrapper_error"
                }
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
            status=(
                "timeout"
                if receipt["outcome"] == "timeout"
                else "indeterminate"
                if receipt["outcome"] == "wrapper_error"
                else "completed"
                if receipt["exit_code"] == 0
                else "failed"
            ),
        )
        if receipt["outcome"] == "timeout":
            return ChannelObservation(
                phase="indeterminate",
                checkpoint=checkpoint,
                failure_code="local_cli_timeout",
            )
        if receipt["outcome"] == "wrapper_error":
            return ChannelObservation(
                phase="indeterminate",
                checkpoint=checkpoint,
                failure_code="local_cli_wrapper_error",
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
            terminal_result=terminal_result(text),
        )

    def _submit_cli(self, snapshot: HandoffSnapshot) -> ChannelObservation:
        with _open_cli_dir(
            self._source_home, snapshot.handoff_id
        ) as (_paths, directory_fd):
            prompt = _read_bounded_at(directory_fd, "prompt.txt")
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
            env={
                **hermes_subprocess_env(inherit_credentials=False),
                "HERMES_HOME": str(self._source_home),
            },
        )
        identity = tree.identity
        if identity.start_time is None or identity.group_id != identity.pid:
            ManagedProcessTree.terminate_existing(identity)
            raise ChannelIndeterminate("local_cli_identity_unavailable")
        with self._cli_trees_lock:
            self._cli_trees[snapshot.handoff_id] = tree
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
        checkpoint = snapshot.checkpoint or {}
        if snapshot.phase == "indeterminate" and "receipt_sha256" in checkpoint:
            return ChannelObservation(
                phase="indeterminate",
                checkpoint=checkpoint,
                failure_code=snapshot.failure_code,
            )
        receipt = self._receipt_observation(snapshot)
        if receipt is not None:
            return receipt
        identity = self._process_identity(snapshot)
        if identity is not None and self._identity_is_current(identity):
            return ChannelObservation(
                phase="cancelling" if cancelling else "active",
                checkpoint=_checkpoint(snapshot, status="running"),
            )
        if identity is not None:
            ManagedProcessTree.terminate_orphaned_posix_group(identity)
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
        try:
            response = RunsClient(connection, deadline).submit(
                handoff_id=snapshot.handoff_id,
                prompt=snapshot.spec.prompt,
                session_id=session_id,
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
                idempotency_key=response["idempotency_key"],
                status=str(response.get("status") or "queued"),
            ),
        )

    def submit(
        self, snapshot: HandoffSnapshot, *, budget_seconds: float
    ) -> ChannelObservation:
        if snapshot.mechanism == "local_cli":
            return self._submit_cli(snapshot)
        return self._submit(snapshot, _Deadline(budget_seconds))

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
            response = RunsClient(connection, deadline).status(run_id)
        except urllib.error.HTTPError as exc:
            if exc.code >= 500:
                raise ChannelRetryableFailure() from exc
            raise ChannelIndeterminate() from exc
        except (OSError, TimeoutError, ValueError) as exc:
            raise ChannelRetryableFailure() from exc

        try:
            return observation_from_status(
                snapshot, response, cancelling=cancelling
            )
        except (TypeError, UnicodeError, ValueError) as exc:
            raise ChannelIndeterminate() from exc

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
            tree = self._take_cli_tree(snapshot.handoff_id)
            terminated = False
            if tree is not None:
                try:
                    tree.terminate("handoff cancelled")
                    terminated = tree.reaped and not tree.tree_active()
                except RuntimeError:
                    terminated = False
            else:
                terminated = ManagedProcessTree.terminate_existing(identity)
            if terminated:
                receipt = self._receipt_observation(snapshot)
                if receipt is not None:
                    return receipt
                if self._identity_is_gone(identity):
                    return ChannelObservation(
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
            RunsClient(connection, deadline).stop(run_id)
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
