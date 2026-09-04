"""Credential-safe helpers for Git-backed Hermes sources."""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from hermes_cli._subprocess_compat import noninteractive_git_env

__all__ = [
    "GitSourceError",
    "ResolvedGitSource",
    "canonical_git_source",
    "checkout_exact_revision",
    "git_head_revision",
    "noninteractive_git_env",
    "resolve_git_executable",
    "resolve_git_source",
    "safe_git_error",
    "scrub_cloned_origin",
    "scrub_git_url",
]


class GitSourceError(Exception):
    """Recoverable failure while resolving or operating on a Git source."""


@dataclass(frozen=True, slots=True)
class ResolvedGitSource:
    clone_url: str
    subdirectory: str | None


EXACT_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_HTTP_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_TRAILING_URL_DELIMITERS = ".,;:!?)]}"

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


def normalize_exact_revision(ref: str) -> str:
    """Require and normalize an immutable, full Git commit SHA."""
    if not isinstance(ref, str) or not EXACT_COMMIT_RE.fullmatch(ref):
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


def git_head_revision(repo: Path, git_exe: str) -> str:
    """Return the exact checked-out Git revision."""
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


def checkout_exact_revision(repo: Path, git_exe: str, revision: str) -> None:
    """Fetch and detach at one immutable commit, then verify the resulting HEAD."""
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
