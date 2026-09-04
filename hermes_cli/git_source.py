"""Credential-safe helpers for Git-backed Hermes sources."""

from __future__ import annotations

import functools
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from hermes_cli._subprocess_compat import (
    kill_process_tree,
    noninteractive_git_env,
    windows_hide_flags,
)

__all__ = [
    "GitSourceError",
    "GitSourceCancelled",
    "GitSourceResourceLimit",
    "GitSourceTimeout",
    "ResolvedGitSource",
    "canonical_git_source",
    "checkout_exact_revision",
    "git_head_revision",
    "git_text_contains_credentials",
    "is_exact_revision",
    "noninteractive_git_env",
    "resolve_git_executable",
    "resolve_git_source",
    "run_git_bounded",
    "safe_git_error",
    "scrub_cloned_origin",
    "scrub_git_url",
    "validate_credential_free_git_source",
]


class GitSourceError(Exception):
    """Recoverable failure while resolving or operating on a Git source."""


class GitSourceCancelled(GitSourceError):
    """A bounded Git command was cancelled."""


class GitSourceTimeout(GitSourceError):
    """A bounded Git command exceeded its timeout."""


class GitSourceResourceLimit(GitSourceError):
    """A bounded Git command exceeded an output or temporary-storage limit."""

    def __init__(
        self,
        kind: Literal["output", "storage"],
        result: subprocess.CompletedProcess[str],
    ):
        self.kind = kind
        self.result = result
        super().__init__(f"Git {kind} limit exceeded.")


@dataclass(frozen=True, slots=True)
class ResolvedGitSource:
    clone_url: str
    subdirectory: str | None


EXACT_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_HTTP_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_GIT_ABSOLUTE_URL_RE = re.compile(r"(?:https?|ssh|file)://[^\s<>\"']+", re.IGNORECASE)
_USER_PASSWORD_AUTHORITY_RE = re.compile(
    r"(?<![A-Za-z0-9._-])[A-Za-z0-9._-]+:[^@\s/]+@[A-Za-z0-9.-]+",
    re.ASCII,
)
_CREDENTIAL_PARAMETER_RE = re.compile(r"[?#&;]([A-Za-z0-9_.~+%\-]+)=")
_TRAILING_URL_DELIMITERS = ".,;:!?)]}"
_DEFAULT_BOUNDED_OUTPUT_BYTES = 64 * 1024
_CREDENTIAL_PARAMETER_WORDS = frozenset({
    "auth",
    "authorization",
    "credential",
    "credentials",
    "key",
    "password",
    "secret",
    "signature",
    "token",
})
_CREDENTIAL_COMPOUND_QUALIFIERS = (
    "access",
    "api",
    "auth",
    "authorization",
    "aws",
    "azure",
    "client",
    "deploy",
    "github",
    "gitlab",
    "google",
    "oauth",
    "private",
    "secret",
    "security",
)
_CREDENTIAL_COMPOUND_SUFFIXES = (
    "credential",
    "credentials",
    "key",
    "password",
    "secret",
    "signature",
    "token",
)

_GITHUB_BROWSER_SEGMENTS = {
    "actions",
    "blob",
    "commit",
    "commits",
    "issues",
    "pull",
    "pulls",
    "releases",
    "tree",
    "wiki",
}


@functools.lru_cache(maxsize=1)
def resolve_git_executable() -> str | None:
    """Resolve a Git binary when ``PATH`` may be minimal."""
    found = shutil.which("git")
    if found:
        return found
    if os.name == "nt":
        prog = os.environ.get("ProgramFiles", r"C:\Program Files")
        prog_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            os.path.join(prog, "Git", "cmd", "git.exe"),
            os.path.join(prog, "Git", "bin", "git.exe"),
            os.path.join(prog_x86, "Git", "cmd", "git.exe"),
            os.path.join(prog_x86, "Git", "bin", "git.exe"),
        ]
        if local:
            candidates.extend((
                os.path.join(local, "Programs", "Git", "cmd", "git.exe"),
                os.path.join(local, "Programs", "Git", "bin", "git.exe"),
            ))
    else:
        candidates = ["/usr/bin/git", "/usr/local/bin/git", "/bin/git"]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def _split_supported_identifier(identifier: str) -> tuple[str, str | None]:
    """Return a clone URL and optional source-relative subdirectory."""
    if identifier.startswith(("https://", "http://", "git@", "ssh://", "file://")):
        if identifier.startswith("https://github.com/"):
            path = identifier[len("https://github.com/") :]
            path = path.split("?", 1)[0].split("#", 1)[0].strip("/")
            parts = path.split("/")
            if (
                len(parts) >= 3
                and all(parts[:2])
                and parts[2] in _GITHUB_BROWSER_SEGMENTS
            ):
                repo = parts[1].removesuffix(".git")
                subdirectory = None
                if parts[2] == "tree" and len(parts) >= 5:
                    subdirectory = (
                        "/".join(p for p in parts[4:] if p).strip("/") or None
                    )
                return f"https://github.com/{parts[0]}/{repo}.git", subdirectory

        if "#" in identifier:
            clone_url, _, fragment = identifier.partition("#")
            return clone_url, fragment

        marker = ".git/"
        index = identifier.find(marker)
        if index != -1:
            clone_url = identifier[: index + len(".git")]
            subdirectory = identifier[index + len(marker) :]
            return clone_url, subdirectory

        return identifier, None

    parts = [part for part in identifier.strip("/").split("/") if part]
    if len(parts) >= 2:
        owner, repo = parts[0], parts[1]
        subdirectory = "/".join(parts[2:])
        return f"https://github.com/{owner}/{repo}.git", subdirectory

    raise GitSourceError(
        f"Invalid Git source: '{identifier}'. "
        "Use a Git URL or 'owner/repo' shorthand (optionally with a subdirectory: "
        "'owner/repo/path/to/source')."
    )


def _normalize_subdirectory(subdirectory: str | None) -> str | None:
    if subdirectory is None:
        return None
    return subdirectory.strip("/") or None


def resolve_git_source(identifier: str) -> ResolvedGitSource:
    """Resolve a supported Git identifier into clone and subdirectory parts."""
    value = identifier.strip()
    if not value:
        raise GitSourceError("Git source must not be empty.")
    clone_url, subdirectory = _split_supported_identifier(value)
    return ResolvedGitSource(
        clone_url=clone_url,
        subdirectory=_normalize_subdirectory(subdirectory),
    )


def is_exact_revision(ref: object) -> bool:
    """Return whether *ref* is one immutable full Git commit SHA."""
    return isinstance(ref, str) and EXACT_COMMIT_RE.fullmatch(ref) is not None


def _parameter_name_words(name: str) -> set[str]:
    decoded = name
    for _ in range(3):
        next_value = urllib.parse.unquote_plus(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    name = decoded
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    separated = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", separated)
    return set(re.findall(r"[a-z0-9]+", separated.casefold()))


def _is_credential_qualifier_sequence(value: str) -> bool:
    reachable = {0}
    for start in range(len(value)):
        if start not in reachable:
            continue
        reachable.update(
            start + len(qualifier)
            for qualifier in _CREDENTIAL_COMPOUND_QUALIFIERS
            if value.startswith(qualifier, start)
        )
    return bool(value) and len(value) in reachable


def _parameter_name_contains_credentials(name: str) -> bool:
    if _parameter_name_words(name) & _CREDENTIAL_PARAMETER_WORDS:
        return True
    compact_name = re.sub(r"[^a-z0-9]+", "", name.casefold())
    return any(
        compact_name.endswith(suffix)
        and _is_credential_qualifier_sequence(compact_name[: -len(suffix)])
        for suffix in _CREDENTIAL_COMPOUND_SUFFIXES
    )


def git_text_contains_credentials(value: str) -> bool:
    """Return whether display text contains credentials unsafe to persist."""

    from agent.redact import redact_sensitive_text

    if redact_sensitive_text(value, force=True) != value:
        return True
    if _USER_PASSWORD_AUTHORITY_RE.search(value):
        return True
    if any(
        _parameter_name_contains_credentials(match.group(1))
        for match in _CREDENTIAL_PARAMETER_RE.finditer(value)
    ):
        return True
    for match in _GIT_ABSOLUTE_URL_RE.finditer(value):
        candidate = match.group(0).rstrip(_TRAILING_URL_DELIMITERS)
        try:
            parsed = urllib.parse.urlsplit(candidate)
        except ValueError:
            return True
        if parsed.scheme.casefold() in {"http", "https", "file"}:
            if parsed.username is not None or parsed.password is not None:
                return True
        elif parsed.scheme.casefold() == "ssh" and parsed.password is not None:
            return True
    return False


def validate_credential_free_git_source(identifier: str) -> str:
    """Validate a Git source identity without ever echoing rejected input."""

    invalid_message = "Git source identity is invalid."
    credential_message = "Git source identity contains credentials."
    if (
        not isinstance(identifier, str)
        or not identifier
        or identifier != identifier.strip()
        or "\x00" in identifier
    ):
        raise GitSourceError(invalid_message)
    try:
        parsed = urllib.parse.urlsplit(identifier)
    except ValueError as error:
        raise GitSourceError(invalid_message) from error
    scheme = parsed.scheme.casefold()
    supported_scheme = scheme in {"http", "https", "ssh", "file"}
    if not supported_scheme and identifier.startswith("git@"):
        if re.fullmatch(r"git@[^@\s/:]+:.+", identifier) is None:
            raise GitSourceError(invalid_message)
    elif not supported_scheme and (scheme or "@" in identifier):
        raise GitSourceError(invalid_message)
    if git_text_contains_credentials(identifier):
        raise GitSourceError(credential_message)
    if scheme in {"ssh", "file"} and parsed.query:
        raise GitSourceError(invalid_message)
    if identifier.startswith("git@") and "?" in identifier.partition("#")[0]:
        raise GitSourceError(invalid_message)
    try:
        resolve_git_source(identifier)
    except GitSourceError as error:
        raise GitSourceError(invalid_message) from error
    return identifier


def _temporary_storage_exceeded(
    root: Path,
    *,
    max_bytes: int,
    max_entries: int,
) -> bool:
    if not root.exists():
        return False
    total_bytes = 0
    entries_seen = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = os.scandir(directory)
        except FileNotFoundError:
            continue
        except OSError:
            return True
        with entries:
            for entry in entries:
                entries_seen += 1
                if entries_seen > max_entries:
                    return True
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                except OSError:
                    return True
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append(Path(entry.path))
                else:
                    total_bytes += metadata.st_size
                    if total_bytes > max_bytes:
                        return True
    return False


def run_git_bounded(
    arguments: list[str],
    *,
    cwd: Path | None = None,
    input_text: str | None = None,
    timeout: float,
    cancelled: Callable[[], bool] = lambda: False,
    max_output_bytes: int = _DEFAULT_BOUNDED_OUTPUT_BYTES,
    storage_root: Path | None = None,
    max_storage_bytes: int | None = None,
    max_storage_entries: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Git with bounded output, storage growth, timeout, and cancellation."""

    if timeout <= 0 or max_output_bytes <= 0:
        raise ValueError("Git command timeout and output limit must be positive")
    if storage_root is not None and (
        max_storage_bytes is None
        or max_storage_entries is None
        or max_storage_bytes <= 0
        or max_storage_entries <= 0
    ):
        raise ValueError("Git storage limits must be positive when a root is supplied")
    try:
        if cancelled():
            raise GitSourceCancelled("Git operation was cancelled.")
    except GitSourceCancelled:
        raise
    except Exception as error:
        raise GitSourceCancelled("Git cancellation check failed.") from error

    try:
        if os.name == "nt":
            process = subprocess.Popen(
                arguments,
                cwd=str(cwd) if cwd is not None else None,
                stdin=(
                    subprocess.PIPE if input_text is not None else subprocess.DEVNULL
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=noninteractive_git_env(),
                creationflags=windows_hide_flags(),
            )
        else:
            process = subprocess.Popen(
                arguments,
                cwd=str(cwd) if cwd is not None else None,
                stdin=(
                    subprocess.PIPE if input_text is not None else subprocess.DEVNULL
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=noninteractive_git_env(),
                process_group=0,
            )
    except OSError as error:
        raise GitSourceError("Could not start Git.") from error

    retained = {"stdout": bytearray(), "stderr": bytearray()}
    retained_size = 0
    total_output = 0
    output_exceeded = threading.Event()
    output_lock = threading.Lock()

    def read_stream(name: Literal["stdout", "stderr"]) -> None:
        nonlocal retained_size, total_output
        stream = getattr(process, name)
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                with output_lock:
                    total_output += len(chunk)
                    remaining = max_output_bytes - retained_size
                    if remaining > 0:
                        kept = chunk[:remaining]
                        retained[name].extend(kept)
                        retained_size += len(kept)
                    if total_output > max_output_bytes:
                        output_exceeded.set()
        except OSError:
            return

    readers = [
        threading.Thread(target=read_stream, args=(name,), daemon=True)
        for name in ("stdout", "stderr")
    ]
    for reader in readers:
        reader.start()

    if input_text is not None:

        def write_input() -> None:
            if process.stdin is None:
                return
            try:
                process.stdin.write(input_text.encode("utf-8"))
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    pass

        threading.Thread(target=write_input, daemon=True).start()

    deadline = time.monotonic() + timeout
    failure: Literal["cancelled", "timeout", "output", "storage"] | None = None
    while process.poll() is None:
        try:
            cancellation_requested = cancelled()
        except Exception:
            cancellation_requested = True
        if cancellation_requested:
            failure = "cancelled"
            break
        if output_exceeded.is_set():
            failure = "output"
            break
        if (
            storage_root is not None
            and max_storage_bytes is not None
            and max_storage_entries is not None
            and _temporary_storage_exceeded(
                storage_root,
                max_bytes=max_storage_bytes,
                max_entries=max_storage_entries,
            )
        ):
            failure = "storage"
            break
        if time.monotonic() >= deadline:
            failure = "timeout"
            break
        time.sleep(0.01)

    if failure is not None:
        kill_process_tree(process)
    try:
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        kill_process_tree(process)
    for reader in readers:
        reader.join(timeout=0.2)
    if failure is None and output_exceeded.is_set():
        failure = "output"
    if (
        failure is None
        and storage_root is not None
        and max_storage_bytes is not None
        and max_storage_entries is not None
        and _temporary_storage_exceeded(
            storage_root,
            max_bytes=max_storage_bytes,
            max_entries=max_storage_entries,
        )
    ):
        failure = "storage"
    result = subprocess.CompletedProcess(
        arguments,
        process.returncode if process.returncode is not None else -1,
        bytes(retained["stdout"]).decode("utf-8", errors="replace"),
        bytes(retained["stderr"]).decode("utf-8", errors="replace"),
    )
    if failure == "cancelled":
        raise GitSourceCancelled("Git operation was cancelled.")
    if failure == "timeout":
        raise GitSourceTimeout(f"Git operation timed out after {timeout} seconds.")
    if failure in {"output", "storage"}:
        raise GitSourceResourceLimit(failure, result)
    return result


def normalize_exact_revision(ref: str) -> str:
    """Require and normalize an immutable, full Git commit SHA."""
    if not is_exact_revision(ref):
        raise GitSourceError("--ref must be a full 40-character commit SHA.")
    return ref.lower()


def safe_git_error(
    result: subprocess.CompletedProcess,
    source_url: str = "",
) -> str:
    """Return diagnosable Git output without echoing embedded credentials."""
    from agent.redact import redact_sensitive_text

    error = (result.stderr or result.stdout or "").strip()
    if source_url:
        error = error.replace(source_url, scrub_git_url(source_url))
    error = _HTTP_URL_RE.sub(_scrub_embedded_http_url, error)
    return redact_sensitive_text(
        error,
        force=True,
    )


def _scrub_embedded_http_url(match: re.Match[str]) -> str:
    value = match.group(0)
    end = len(value)
    while end and value[end - 1] in _TRAILING_URL_DELIMITERS:
        end -= 1
    return f"{scrub_git_url(value[:end])}{value[end:]}"


def git_head_revision(
    repo: Path,
    git_exe: str,
    *,
    cancelled: Callable[[], bool] | None = None,
    timeout: float = 15,
    max_output_bytes: int = _DEFAULT_BOUNDED_OUTPUT_BYTES,
    storage_root: Path | None = None,
    max_storage_bytes: int | None = None,
    max_storage_entries: int | None = None,
) -> str:
    """Return the exact checked-out Git revision."""
    use_bounded_runner = (
        cancelled is not None
        or timeout != 15
        or max_output_bytes != _DEFAULT_BOUNDED_OUTPUT_BYTES
        or storage_root is not None
    )
    if use_bounded_runner:
        result = run_git_bounded(
            [git_exe, "rev-parse", "HEAD"],
            cwd=repo,
            timeout=timeout,
            cancelled=cancelled or (lambda: False),
            max_output_bytes=max_output_bytes,
            storage_root=storage_root,
            max_storage_bytes=max_storage_bytes,
            max_storage_entries=max_storage_entries,
        )
    else:
        result = subprocess.run(
            [git_exe, "rev-parse", "HEAD"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            stdin=subprocess.DEVNULL,
            env=noninteractive_git_env(),
        )
    if result.returncode != 0:
        error = safe_git_error(result)
        raise GitSourceError(f"Could not determine installed Git revision:\n{error}")
    return result.stdout.strip().lower()


def checkout_exact_revision(
    repo: Path,
    git_exe: str,
    revision: str,
    *,
    cancelled: Callable[[], bool] | None = None,
    timeout: float = 60,
    max_output_bytes: int = _DEFAULT_BOUNDED_OUTPUT_BYTES,
    storage_root: Path | None = None,
    max_storage_bytes: int | None = None,
    max_storage_entries: int | None = None,
    before_checkout: Callable[[], None] | None = None,
) -> None:
    """Fetch and detach at one immutable commit, then verify the resulting HEAD."""
    use_bounded_runner = (
        cancelled is not None
        or timeout != 60
        or max_output_bytes != _DEFAULT_BOUNDED_OUTPUT_BYTES
        or storage_root is not None
    )
    if use_bounded_runner:
        fetched = run_git_bounded(
            [git_exe, "fetch", "--depth", "1", "origin", revision],
            cwd=repo,
            timeout=timeout,
            cancelled=cancelled or (lambda: False),
            max_output_bytes=max_output_bytes,
            storage_root=storage_root,
            max_storage_bytes=max_storage_bytes,
            max_storage_entries=max_storage_entries,
        )
    else:
        try:
            fetched = subprocess.run(
                [git_exe, "fetch", "--depth", "1", "origin", revision],
                cwd=str(repo),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                stdin=subprocess.DEVNULL,
                env=noninteractive_git_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise GitSourceError(
                f"Git fetch of commit '{revision}' timed out after 60 seconds."
            ) from exc
    if fetched.returncode != 0:
        error = safe_git_error(fetched)
        raise GitSourceError(f"Git commit '{revision}' could not be fetched:\n{error}")
    if before_checkout is not None:
        before_checkout()
    if use_bounded_runner:
        checked_out = run_git_bounded(
            [git_exe, "checkout", "--detach", revision],
            cwd=repo,
            timeout=timeout,
            cancelled=cancelled or (lambda: False),
            max_output_bytes=max_output_bytes,
            storage_root=storage_root,
            max_storage_bytes=max_storage_bytes,
            max_storage_entries=max_storage_entries,
        )
    else:
        try:
            checked_out = subprocess.run(
                [git_exe, "checkout", "--detach", revision],
                cwd=str(repo),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                stdin=subprocess.DEVNULL,
                env=noninteractive_git_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise GitSourceError(
                f"Git checkout of commit '{revision}' timed out after 60 seconds."
            ) from exc
    if checked_out.returncode != 0:
        error = safe_git_error(checked_out)
        raise GitSourceError(f"Git checkout of commit '{revision}' failed:\n{error}")
    if use_bounded_runner:
        actual = git_head_revision(
            repo,
            git_exe,
            cancelled=cancelled or (lambda: False),
            timeout=timeout,
            max_output_bytes=max_output_bytes,
            storage_root=storage_root,
            max_storage_bytes=max_storage_bytes,
            max_storage_entries=max_storage_entries,
        )
    else:
        actual = git_head_revision(repo, git_exe)
    if actual != revision:
        raise GitSourceError(
            f"Checked-out revision '{actual}' does not match requested commit '{revision}'."
        )


def scrub_git_url(git_url: str) -> str:
    """Strip credentials and query/fragment data from an HTTP Git URL."""
    parsed = urllib.parse.urlsplit(git_url)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    return git_url


def canonical_git_source(git_url: str, subdirectory: str | None) -> str:
    """Return a credential-free, stable identity for a Git source."""
    scrubbed = scrub_git_url(git_url)
    return f"{scrubbed}#{subdirectory}" if subdirectory else scrubbed


def scrub_cloned_origin(repo: Path, git_exe: str, git_url: str) -> None:
    """Ensure credentials used for cloning do not survive in ``.git/config``."""
    scrubbed = scrub_git_url(git_url)
    if scrubbed == git_url:
        return
    result = subprocess.run(
        [git_exe, "remote", "set-url", "origin", scrubbed],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        stdin=subprocess.DEVNULL,
        env=noninteractive_git_env(),
    )
    if result.returncode != 0:
        error = safe_git_error(result, git_url)
        raise GitSourceError(f"Could not sanitize installed Git remote:\n{error}")
