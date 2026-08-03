"""Descriptor-authoritative rendering for Archon v3 Bash substitutions."""

from __future__ import annotations

from bisect import bisect_left
import hashlib
import os
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


BASH_INLINE_MAX_BYTES = 32_768
BASH_SPILL_MAX_FILES = 64
BASH_SPILL_MAX_VALUE_BYTES = 500_000
BASH_SPILL_MAX_TOTAL_BYTES = 2_000_000
_BASH_LEXER_MAX_NESTING = 64
_NATIVE_WINDOWS = os.name == "nt"
_DESCRIPTOR_SPILLS_SUPPORTED = (
    hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and os.open in os.supports_dir_fd
    and os.mkdir in os.supports_dir_fd
)


class BashRenderingError(ValueError):
    """One stable fail-closed Archon v3 Bash rendering failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(slots=True)
class _NestingFrame:
    kind: str
    resume_quote: str | None
    parenthesis_depth: int = 1


@dataclass(frozen=True, slots=True)
class RenderedBashCommand:
    """Exact shell command and caller-owned read-only spill descriptors."""

    command: str
    inherited_descriptors: tuple[int, ...] = ()
    template_sha256: str = ""
    template_size_bytes: int = 0
    rendered_sha256: str = ""
    rendered_size_bytes: int = 0
    spill_count: int = 0
    spill_total_bytes: int = 0
    spill_content_sha256: tuple[str, ...] = ()
    descriptor_manifest: tuple[tuple[int, str], ...] = ()

    def evidence(self) -> dict[str, object]:
        """Return bounded metadata without values or spill pathnames."""
        return {
            "template_sha256": self.template_sha256,
            "template_size_bytes": self.template_size_bytes,
            "rendered_sha256": self.rendered_sha256,
            "rendered_size_bytes": self.rendered_size_bytes,
            "spill_count": self.spill_count,
            "spill_total_bytes": self.spill_total_bytes,
            "spill_content_sha256": list(self.spill_content_sha256),
            "descriptor_manifest": [
                {"descriptor": descriptor, "sha256": digest}
                for descriptor, digest in self.descriptor_manifest
            ],
        }


def _quote_inline_value(value: str, quote: str | None) -> str:
    if quote == "'":
        return value.replace("'", "'\\''")
    if quote == '"':
        return (
            value
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("$", "\\$")
            .replace("`", "\\`")
        )
    return shlex.quote(value)


def classify_bash_reference_spans(
    template: str,
    spans: Iterable[tuple[int, int]],
) -> tuple[tuple[int, int, str | None], ...]:
    """Return admitted Bash spans and quote contexts, ignoring literal spans."""
    ordered = tuple(spans)
    if any(
        type(start) is not int
        or type(end) is not int
        or start < 0
        or end <= start
        or end > len(template)
        for start, end in ordered
    ):
        raise ValueError("bash substitution offsets are invalid")
    if ordered != tuple(sorted(ordered)) or any(
        previous_end > start
        for (_previous_start, previous_end), (start, _end) in zip(
            ordered,
            ordered[1:],
        )
    ):
        raise ValueError("bash substitution offsets must be ordered and disjoint")
    by_start = {start: (start, end) for start, end in ordered}
    if len(by_start) != len(ordered):
        raise ValueError("bash substitution offsets must be unique")
    reference_starts = tuple(by_start)
    decisions: dict[int, str | None | bool] = {}
    quote: str | None = None
    frames: list[_NestingFrame] = []
    pending_heredocs: list[tuple[str, bool]] = []
    word_start = True
    position = 0

    def decide_range(start: int, end: int, decision: bool) -> None:
        first = bisect_left(reference_starts, start)
        last = bisect_left(reference_starts, end, lo=first)
        for reference_start in reference_starts[first:last]:
            decisions[reference_start] = decision

    def unsupported_range(start: int, end: int) -> None:
        decide_range(start, end, False)

    def consume_heredocs(start: int) -> int:
        cursor = start
        for delimiter, strip_tabs in pending_heredocs:
            body_start = cursor
            while cursor <= len(template):
                newline = template.find("\n", cursor)
                line_end = len(template) if newline < 0 else newline
                line = template[cursor:line_end]
                compared = line.lstrip("\t") if strip_tabs else line
                if compared == delimiter:
                    unsupported_range(body_start, line_end)
                    cursor = len(template) if newline < 0 else newline + 1
                    break
                if newline < 0:
                    raise BashRenderingError(
                        "bash_reference_context_unsupported",
                        "Bash reference appears in an unterminated here-document",
                    )
                cursor = newline + 1
            else:  # pragma: no cover - loop boundary defense
                raise BashRenderingError(
                    "bash_reference_context_unsupported",
                    "Bash reference appears in an ambiguous here-document",
                )
        pending_heredocs.clear()
        return cursor

    while position < len(template):
        if position in by_start:
            if frames:
                decisions[position] = False
            else:
                decisions[position] = quote

        character = template[position]
        if quote != "'" and character == "\\":
            escaped_position = position + 1
            if escaped_position in by_start:
                decisions[escaped_position] = True
            escaped_character = (
                template[escaped_position] if escaped_position < len(template) else ""
            )
            position = min(len(template), position + 2)
            if escaped_character != "\n":
                word_start = False
            continue

        comments_allowed = not frames or frames[-1].kind in {"command", "backtick"}
        if quote is None and comments_allowed and character == "#" and word_start:
            newline = template.find("\n", position)
            end = len(template) if newline < 0 else newline
            decide_range(position, end, True)
            if newline < 0:
                position = len(template)
                continue
            position = newline
            character = "\n"

        if quote == "'":
            if character == "'":
                quote = None
            position += 1
            word_start = False
            continue
        if character == "'" and quote is None:
            quote = "'"
            position += 1
            word_start = False
            continue
        if character == '"':
            quote = None if quote == '"' else '"'
            position += 1
            word_start = False
            continue

        if character == "`":
            if frames and frames[-1].kind == "backtick":
                frame = frames.pop()
                quote = frame.resume_quote
                word_start = False
            else:
                if len(frames) >= _BASH_LEXER_MAX_NESTING:
                    raise BashRenderingError(
                        "bash_reference_context_unsupported",
                        "Bash reference nesting exceeds its lexer bound",
                    )
                frames.append(_NestingFrame("backtick", quote))
                quote = None
                word_start = True
            position += 1
            continue

        if character == "$" and position + 1 < len(template):
            if template.startswith("$((", position):
                kind = "arithmetic"
                width = 3
            elif template.startswith("$(", position):
                kind = "command"
                width = 2
            elif template.startswith("${", position):
                kind = "parameter"
                width = 2
            else:
                kind = ""
                width = 0
            if kind:
                if len(frames) >= _BASH_LEXER_MAX_NESTING:
                    raise BashRenderingError(
                        "bash_reference_context_unsupported",
                        "Bash reference nesting exceeds its lexer bound",
                    )
                frames.append(_NestingFrame(kind, quote))
                quote = None
                position += width
                word_start = kind == "command"
                continue

        if frames and quote is None:
            frame = frames[-1]
            if frame.kind == "parameter" and character == "}":
                frames.pop()
                quote = frame.resume_quote
                position += 1
                continue
            if frame.kind == "command":
                closed = False
                if character == "(":
                    frame.parenthesis_depth += 1
                elif character == ")":
                    frame.parenthesis_depth -= 1
                    if frame.parenthesis_depth == 0:
                        frames.pop()
                        quote = frame.resume_quote
                        closed = True
                if closed:
                    word_start = False
                elif character in " \t\r\n;|&()<>":
                    word_start = True
                else:
                    word_start = False
                position += 1
                continue
            if frame.kind == "arithmetic":
                if character == "(":
                    frame.parenthesis_depth += 1
                elif character == ")":
                    if (
                        frame.parenthesis_depth == 1
                        and position + 1 < len(template)
                        and template[position + 1] == ")"
                    ):
                        frames.pop()
                        quote = frame.resume_quote
                        position += 2
                        continue
                    frame.parenthesis_depth -= 1
                position += 1
                continue

        if (
            not frames
            and quote is None
            and template.startswith("<<", position)
            and not template.startswith("<<<", position)
        ):
            cursor = position + 2
            strip_tabs = cursor < len(template) and template[cursor] == "-"
            if strip_tabs:
                cursor += 1
            while cursor < len(template) and template[cursor] in " \t":
                cursor += 1
            delimiter_start = cursor
            delimiter: list[str] = []
            delimiter_quote: str | None = None
            while cursor < len(template):
                item = template[cursor]
                if delimiter_quote is None and item in " \t\r\n;&|<>()":
                    break
                if item == "\\" and delimiter_quote != "'":
                    cursor += 1
                    if cursor >= len(template) or template[cursor] == "\n":
                        break
                    delimiter.append(template[cursor])
                elif item == "'" and delimiter_quote != '"':
                    delimiter_quote = None if delimiter_quote == "'" else "'"
                elif item == '"' and delimiter_quote != "'":
                    delimiter_quote = None if delimiter_quote == '"' else '"'
                else:
                    delimiter.append(item)
                cursor += 1
            unsupported_range(delimiter_start, cursor)
            if not delimiter or delimiter_quote is not None:
                raise BashRenderingError(
                    "bash_reference_context_unsupported",
                    "Bash reference appears near an ambiguous here-document delimiter",
                )
            pending_heredocs.append(("".join(delimiter), strip_tabs))
            position = cursor
            word_start = False
            continue

        if character == "\n":
            position += 1
            word_start = True
            if pending_heredocs:
                position = consume_heredocs(position)
            continue
        if character in " \t\r;|&()<>" and quote is None:
            word_start = True
        else:
            word_start = False
        position += 1

    if ordered and (quote is not None or frames or pending_heredocs):
        raise BashRenderingError(
            "bash_reference_context_unsupported",
            "Bash reference appears in an unterminated or ambiguous shell state",
        )
    if any(start not in decisions for start in reference_starts) or any(
        decision is False for decision in decisions.values()
    ):
        raise BashRenderingError(
            "bash_reference_context_unsupported",
            "Bash reference appears in an unsupported shell context",
        )
    return tuple(
        (start, end, decisions[start] if isinstance(decisions[start], str) else None)
        for start, end in ordered
        if decisions.get(start) is not True
    )


def _open_flags(*, directory: bool = False) -> int:
    flags = os.O_RDONLY
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return flags


def _write_all(descriptor: int, data: bytes) -> None:
    position = 0
    while position < len(data):
        written = os.write(descriptor, data[position:])
        if written <= 0:
            raise OSError("short write while materializing Bash spill")
        position += written


def _open_directory_chain(path: Path) -> int:
    """Open every path component relative to a verified no-follow descriptor."""
    if path.is_absolute():
        descriptor = os.open(path.anchor, _open_flags(directory=True))
        components = path.parts[1:]
    else:
        descriptor = os.open(".", _open_flags(directory=True))
        components = path.parts
    try:
        for component in components:
            if component in {"", "."}:
                continue
            if component == "..":
                raise OSError("Bash spill directory cannot traverse a parent")
            child = os.open(
                component,
                _open_flags(directory=True),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _verified_spill_descriptor(
    directory_descriptor: int,
    filename: str,
    data: bytes,
    digest: str,
) -> int:
    create_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    write_descriptor = os.open(
        filename,
        create_flags,
        0o600,
        dir_fd=directory_descriptor,
    )
    read_descriptor: int | None = None
    try:
        os.fchmod(write_descriptor, 0o600)
        _write_all(write_descriptor, data)
        os.fsync(write_descriptor)
        written = os.fstat(write_descriptor)
        if (
            not stat.S_ISREG(written.st_mode)
            or written.st_nlink != 1
            or written.st_size != len(data)
        ):
            raise OSError("Bash spill failed regular-file verification")
        read_descriptor = os.open(
            filename,
            _open_flags(),
            dir_fd=directory_descriptor,
        )
        reopened = os.fstat(read_descriptor)
        if (
            not stat.S_ISREG(reopened.st_mode)
            or reopened.st_nlink != 1
            or (reopened.st_dev, reopened.st_ino) != (written.st_dev, written.st_ino)
            or reopened.st_size != len(data)
        ):
            raise OSError("Bash spill identity changed during verification")
        observed = hashlib.sha256()
        remaining = len(data)
        while remaining:
            chunk = os.read(read_descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise OSError("Bash spill ended before its verified size")
            observed.update(chunk)
            remaining -= len(chunk)
        if os.read(read_descriptor, 1) or observed.hexdigest() != digest:
            raise OSError("Bash spill content failed verification")
        os.lseek(read_descriptor, 0, os.SEEK_SET)
        return read_descriptor
    except BaseException:
        if read_descriptor is not None:
            os.close(read_descriptor)
        raise
    finally:
        os.close(write_descriptor)


def _materialize_spills(
    spill_directory: Path,
    spills: tuple[tuple[bytes, str], ...],
) -> tuple[int, ...]:
    if (
        len(spills) > BASH_SPILL_MAX_FILES
        or any(len(data) > BASH_SPILL_MAX_VALUE_BYTES for data, _digest in spills)
        or sum(len(data) for data, _digest in spills) > BASH_SPILL_MAX_TOTAL_BYTES
    ):
        raise BashRenderingError(
            "bash_substitution_limit",
            "Bash spill materialization exceeds its bounded limits",
        )
    parent = spill_directory.parent
    parent_descriptor = _open_directory_chain(parent)
    directory_descriptor: int | None = None
    descriptors: list[int] = []
    try:
        os.mkdir(spill_directory.name, 0o700, dir_fd=parent_descriptor)
        directory_descriptor = os.open(
            spill_directory.name,
            _open_flags(directory=True),
            dir_fd=parent_descriptor,
        )
        directory_stat = os.fstat(directory_descriptor)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise OSError("Bash spill root is not a directory")
        for index, (data, digest) in enumerate(spills):
            descriptors.append(
                _verified_spill_descriptor(
                    directory_descriptor,
                    f"spill-{index:04d}",
                    data,
                    digest,
                )
            )
        return tuple(descriptors)
    except BaseException:
        for descriptor in descriptors:
            os.close(descriptor)
        raise
    finally:
        if directory_descriptor is not None:
            os.close(directory_descriptor)
        os.close(parent_descriptor)


def render_v3_bash(
    template: str,
    substitutions: Iterable[tuple[int, int, str]],
    *,
    spill_directory: str | Path,
) -> RenderedBashCommand:
    """Render one v3 command without reopening a spill pathname in the shell."""
    requested = tuple(substitutions)
    admitted = classify_bash_reference_spans(
        template,
        ((start, end) for start, end, _value in requested),
    )
    requested_by_span = {(start, end): value for start, end, value in requested}
    ordered = tuple(
        (start, end, requested_by_span[(start, end)], quote)
        for start, end, quote in admitted
    )
    spill_by_value: dict[bytes, tuple[str, int]] = {}
    spill_values: list[tuple[bytes, str]] = []
    for _start, _end, value, _quote in ordered:
        encoded = value.encode("utf-8")
        if b"\0" in encoded:
            raise BashRenderingError(
                "bash_substitution_nul",
                "Bash substitutions cannot contain NUL bytes",
            )
        if len(encoded) > BASH_SPILL_MAX_VALUE_BYTES:
            raise BashRenderingError(
                "bash_substitution_limit",
                "Bash substitution exceeds its per-value byte limit",
            )
        if len(encoded) <= BASH_INLINE_MAX_BYTES or encoded in spill_by_value:
            continue
        digest = hashlib.sha256(encoded).hexdigest()
        spill_by_value[encoded] = (digest, len(spill_values))
        spill_values.append((encoded, digest))

    spill_total_bytes = sum(len(data) for data, _digest in spill_values)
    if (
        len(spill_values) > BASH_SPILL_MAX_FILES
        or spill_total_bytes > BASH_SPILL_MAX_TOTAL_BYTES
    ):
        raise BashRenderingError(
            "bash_substitution_limit",
            "Bash substitutions exceed aggregate spill limits",
        )
    if spill_values and (_NATIVE_WINDOWS or not _DESCRIPTOR_SPILLS_SUPPORTED):
        raise BashRenderingError(
            "bash_spill_integrity",
            "Secure Bash spill descriptors are unavailable on this host",
        )
    try:
        descriptors = (
            _materialize_spills(
                Path(spill_directory),
                tuple(spill_values),
            )
            if spill_values
            else ()
        )
    except (OSError, NotImplementedError) as exc:
        raise BashRenderingError(
            "bash_spill_integrity",
            "Bash spill descriptor materialization failed integrity checks",
        ) from exc
    variables = {
        data: f"__HERMES_WF_SPILL_{digest}"
        for data, (digest, _index) in spill_by_value.items()
    }
    prologue = "".join(
        (
            f"{variables[data]}=$(command cat <&{descriptors[index]}; "
            '__hermes_rc=$?; printf x; exit "$__hermes_rc") || exit $?\n'
            f"{variables[data]}=${{{variables[data]}%x}}\n"
        )
        for data, (_digest, index) in spill_by_value.items()
    )
    rendered: list[str] = [prologue]
    position = 0
    for start, end, value, quote in ordered:
        rendered.append(template[position:start])
        encoded = value.encode("utf-8")
        if encoded in variables:
            variable = variables[encoded]
            replacement = {
                None: f'"${{{variable}}}"',
                '"': f"${{{variable}}}",
                "'": f"'\"${{{variable}}}\"'",
            }[quote]
        else:
            replacement = _quote_inline_value(value, quote)
        rendered.append(replacement)
        position = end
    rendered.append(template[position:])
    rendered_command = "".join(rendered)
    template_bytes = template.encode("utf-8")
    rendered_bytes = rendered_command.encode("utf-8")
    spill_digests = tuple(digest for _data, digest in spill_values)
    return RenderedBashCommand(
        command=rendered_command,
        inherited_descriptors=descriptors,
        template_sha256=hashlib.sha256(template_bytes).hexdigest(),
        template_size_bytes=len(template_bytes),
        rendered_sha256=hashlib.sha256(rendered_bytes).hexdigest(),
        rendered_size_bytes=len(rendered_bytes),
        spill_count=len(spill_values),
        spill_total_bytes=spill_total_bytes,
        spill_content_sha256=spill_digests,
        descriptor_manifest=tuple(zip(descriptors, spill_digests, strict=True)),
    )


__all__ = [
    "BASH_INLINE_MAX_BYTES",
    "BASH_SPILL_MAX_FILES",
    "BASH_SPILL_MAX_TOTAL_BYTES",
    "BASH_SPILL_MAX_VALUE_BYTES",
    "BashRenderingError",
    "RenderedBashCommand",
    "classify_bash_reference_spans",
    "render_v3_bash",
]
