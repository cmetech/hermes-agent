"""Descriptor-authoritative rendering for Archon v3 Bash substitutions."""

from __future__ import annotations

from bisect import bisect_left
import hashlib
import os
import re
import selectors
import shlex
import stat
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterable


if TYPE_CHECKING:
    from tools.managed_process import InheritedDescriptorIdentity


BASH_INLINE_MAX_BYTES = 32_768
BASH_SPILL_MAX_FILES = 64
BASH_SPILL_MAX_VALUE_BYTES = 500_000
BASH_SPILL_MAX_TOTAL_BYTES = 2_000_000
_BASH_LEXER_MAX_NESTING = 64
_BASH_SCALAR_REFERENCE = re.compile(
    r"\$(?:(?P<position>[1-9][0-9]*)|(?P<name>[A-Z][A-Z0-9_]*))"
)
_BASH_SCALAR_NAMES = frozenset({
    "ARGUMENTS",
    "USER_MESSAGE",
    "ARTIFACTS_DIR",
    "WORKFLOW_ID",
    "BASE_BRANCH",
    "DOCS_DIR",
    "CONTEXT",
    "LOOP_USER_INPUT",
    "LOOP_PREV_OUTPUT",
    "REJECTION_REASON",
})
_NATIVE_WINDOWS = os.name == "nt"
_DESCRIPTOR_SPILLS_SUPPORTED = (
    hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "pipe")
    and hasattr(os, "set_blocking")
    and os.open in os.supports_dir_fd
    and os.mkdir in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
    and os.rmdir in os.supports_dir_fd
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
    bracket_depth: int = 1
    case_states: list[str] = field(default_factory=list)
    command_position: bool = True
    pending_heredocs: list[tuple[str, bool]] = field(default_factory=list)


class _SpillTransport:
    """Own anonymous verified-byte publication into inherited read pipes."""

    def __init__(
        self,
        read_descriptors: tuple[int, ...],
        read_descriptor_identities: tuple[InheritedDescriptorIdentity, ...],
        publications: tuple[tuple[int, bytes], ...],
    ) -> None:
        self.read_descriptors = read_descriptors
        self.read_descriptor_identities = read_descriptor_identities
        self._publications = publications
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._error: str | None = None
        self._closed = False
        self._reads_released = False

    @classmethod
    def from_snapshots(cls, snapshots: tuple[bytes, ...]) -> _SpillTransport:
        from tools.managed_process import InheritedDescriptorIdentity

        reads: list[int] = []
        identities: list[InheritedDescriptorIdentity] = []
        publications: list[tuple[int, bytes]] = []
        try:
            for snapshot in snapshots:
                read_descriptor, write_descriptor = os.pipe()
                reads.append(read_descriptor)
                identities.append(InheritedDescriptorIdentity.capture(read_descriptor))
                publications.append((write_descriptor, snapshot))
                os.set_inheritable(read_descriptor, False)
                os.set_inheritable(write_descriptor, False)
                os.set_blocking(write_descriptor, False)
            return cls(tuple(reads), tuple(identities), tuple(publications))
        except BaseException:
            for descriptor in reads:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            for descriptor, _snapshot in publications:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise

    def _record_error(self, exc: BaseException) -> None:
        with self._lock:
            if self._error is None:
                self._error = f"{type(exc).__name__}: {exc}"

    def _publish(self, selector: selectors.BaseSelector) -> None:
        pending = {
            descriptor: memoryview(snapshot)
            for descriptor, snapshot in self._publications
        }
        held_after_error: set[int] = set()
        try:
            while pending and not self._stop.is_set():
                for key, _mask in selector.select(timeout=0.05):
                    descriptor = int(key.fd)
                    payload = pending.get(descriptor)
                    if payload is None:
                        continue
                    try:
                        written = os.write(descriptor, payload[: 64 * 1024])
                        if written <= 0:
                            raise OSError("short write while publishing Bash spill")
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        # The shell's read-status path remains authoritative when
                        # its descriptor was closed or replaced before `cat`.
                        selector.unregister(descriptor)
                        pending.pop(descriptor, None)
                        os.close(descriptor)
                        continue
                    except BaseException as exc:
                        self._record_error(exc)
                        selector.unregister(descriptor)
                        pending.pop(descriptor, None)
                        # Keep the writer open so `cat` cannot observe a clean,
                        # truncated EOF before the executor notices the error.
                        held_after_error.add(descriptor)
                        continue
                    remaining = payload[written:]
                    if remaining:
                        pending[descriptor] = remaining
                    else:
                        selector.unregister(descriptor)
                        pending.pop(descriptor, None)
                        os.close(descriptor)
        except BaseException as exc:  # pragma: no cover - selector host failure
            self._record_error(exc)
            held_after_error.update(pending)
        finally:
            if held_after_error:
                self._stop.wait()
            selector.close()
            for descriptor in set(pending).union(held_after_error):
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def start(self) -> None:
        """Start one bounded publisher only after all pre-spawn work succeeds."""
        if not self._publications or self._thread is not None:
            return
        if self._closed:
            raise BashRenderingError(
                "bash_spill_integrity",
                "Bash spill publication ownership was already closed",
            )
        selector = selectors.DefaultSelector()
        try:
            for descriptor, _snapshot in self._publications:
                selector.register(descriptor, selectors.EVENT_WRITE)
            thread = threading.Thread(
                target=self._publish,
                args=(selector,),
                name="hermes-bash-spill-publisher",
                daemon=False,
            )
            thread.start()
        except BaseException as exc:
            selector.close()
            self.close()
            raise BashRenderingError(
                "bash_spill_integrity",
                "Bash spill publication could not start",
            ) from exc
        self._thread = thread

    def publication_error(self) -> str | None:
        with self._lock:
            return self._error

    def release_inherited(self) -> None:
        """Close parent read ends after Task 10 pins and exec-confirms them."""
        with self._lock:
            if self._reads_released:
                return
            self._reads_released = True
        for descriptor in self.read_descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def close(self) -> None:
        """Abort or finish publication and synchronously join its sole thread."""
        if self._closed:
            return
        self._closed = True
        self.release_inherited()
        self._stop.set()
        if self._thread is None:
            for descriptor, _snapshot in self._publications:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            return
        self._thread.join()


@dataclass(frozen=True, slots=True)
class RenderedBashCommand:
    """Exact shell command and caller-owned read-only spill descriptors."""

    command: str
    inherited_descriptors: tuple[int, ...] = ()
    inherited_descriptor_identities: tuple[InheritedDescriptorIdentity, ...] = field(
        default=(),
        repr=False,
        compare=False,
    )
    template_sha256: str = ""
    template_size_bytes: int = 0
    rendered_sha256: str = ""
    rendered_size_bytes: int = 0
    spill_count: int = 0
    spill_total_bytes: int = 0
    spill_content_sha256: tuple[str, ...] = ()
    descriptor_manifest: tuple[tuple[int, str], ...] = ()
    _transport: _SpillTransport | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    _descriptor_lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
        compare=False,
    )
    _descriptors_released: bool = field(
        default=False,
        repr=False,
        compare=False,
    )

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

    def start_publication(self) -> None:
        if self._transport is not None:
            self._transport.start()

    def publication_error(self) -> str | None:
        return (
            self._transport.publication_error() if self._transport is not None else None
        )

    def release_inherited_descriptors(self) -> None:
        if self._transport is not None:
            self._transport.release_inherited()
            return
        with self._descriptor_lock:
            if self._descriptors_released:
                return
            object.__setattr__(self, "_descriptors_released", True)
        for descriptor in self.inherited_descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
        else:
            self.release_inherited_descriptors()


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


def _validate_bash_reference_spans(
    template: str,
    spans: Iterable[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
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
    return ordered


def _logical_bash_input(template: str) -> tuple[str, tuple[int, ...]]:
    """Remove physical continuations and map every physical boundary."""
    logical: list[str] = []
    physical_to_logical = [0] * (len(template) + 1)
    position = 0
    consecutive_backslashes = 0
    while position < len(template):
        physical_to_logical[position] = len(logical)
        if (
            template[position] == "\\"
            and position + 1 < len(template)
            and template[position + 1] == "\n"
            and consecutive_backslashes % 2 == 0
        ):
            physical_to_logical[position + 1] = len(logical)
            physical_to_logical[position + 2] = len(logical)
            position += 2
            consecutive_backslashes = 0
            continue
        character = template[position]
        logical.append(character)
        position += 1
        physical_to_logical[position] = len(logical)
        consecutive_backslashes = (
            consecutive_backslashes + 1 if character == "\\" else 0
        )
    return "".join(logical), tuple(physical_to_logical)


def classify_bash_reference_spans(
    template: str,
    spans: Iterable[tuple[int, int]],
) -> tuple[tuple[int, int, str | None], ...]:
    """Return admitted physical spans after classifying shell logical input."""
    ordered = _validate_bash_reference_spans(template, spans)
    if "\\\n" not in template:
        return _classify_logical_bash_reference_spans(template, ordered)

    logical_template, physical_to_logical = _logical_bash_input(template)
    logical_to_physical: dict[tuple[int, int], tuple[int, int]] = {}
    logical_spans: list[tuple[int, int]] = []
    for start, end in ordered:
        logical_span = (physical_to_logical[start], physical_to_logical[end])
        if logical_span[0] >= logical_span[1] or logical_span in logical_to_physical:
            raise ValueError("bash substitution offsets collapse in logical input")
        logical_to_physical[logical_span] = (start, end)
        logical_spans.append(logical_span)

    admitted = _classify_logical_bash_reference_spans(
        logical_template,
        tuple(logical_spans),
    )
    return tuple(
        (*logical_to_physical[(start, end)], quote) for start, end, quote in admitted
    )


def _classify_logical_bash_reference_spans(
    template: str,
    spans: Iterable[tuple[int, int]],
) -> tuple[tuple[int, int, str | None], ...]:
    """Classify spans in input after shell continuation removal."""
    ordered = _validate_bash_reference_spans(template, spans)
    by_start = {start: (start, end) for start, end in ordered}
    if len(by_start) != len(ordered):
        raise ValueError("bash substitution offsets must be unique")
    reference_starts = tuple(by_start)
    decisions: dict[int, str | None | bool] = {}
    quote: str | None = None
    frames: list[_NestingFrame] = []
    top_level_heredocs: list[tuple[str, bool]] = []
    word_start = True
    position = 0
    compound_assignment_depth = 0
    array_subscript_start: int | None = None
    array_subscript_depth = 0
    top_level_command_position = True
    top_level_assignment_builtin = False
    top_level_word_start: int | None = None

    def decide_range(start: int, end: int, decision: bool) -> None:
        first = bisect_left(reference_starts, start)
        last = bisect_left(reference_starts, end, lo=first)
        for reference_start in reference_starts[first:last]:
            decisions[reference_start] = decision

    def unsupported_range(start: int, end: int) -> None:
        decide_range(start, end, False)

    def consume_heredocs(
        start: int,
        pending_heredocs: list[tuple[str, bool]],
    ) -> int:
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

    def begin_heredoc(start: int) -> int | None:
        if not template.startswith("<<", start) or template.startswith("<<<", start):
            return None
        cursor = start + 2
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
                following = template[cursor + 1] if cursor + 1 < len(template) else ""
                if not following:
                    delimiter_quote = "ambiguous"
                    break
                if following == "\n":
                    cursor += 2
                    continue
                if delimiter_quote == '"' and following not in '$`"\\':
                    delimiter.extend(("\\", following))
                else:
                    delimiter.append(following)
                cursor += 2
                continue
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
        pending_heredocs = (
            frames[-1].pending_heredocs
            if frames and frames[-1].kind == "command"
            else top_level_heredocs
        )
        pending_heredocs.append(("".join(delimiter), strip_tabs))
        return cursor

    def shell_word_at(start: int, word: str) -> bool:
        if not template.startswith(word, start):
            return False
        before = template[start - 1] if start else " "
        end = start + len(word)
        after = template[end] if end < len(template) else " "
        separators = " \t\r\n;|&()<>"
        return before in separators and after in separators

    def assignment_name_before(end: int) -> bool:
        cursor = end
        while cursor > 0 and (
            template[cursor - 1].isascii()
            and (template[cursor - 1].isalnum() or template[cursor - 1] == "_")
        ):
            cursor -= 1
        if cursor == end or not (
            template[cursor].isascii()
            and (template[cursor].isalpha() or template[cursor] == "_")
        ):
            return False
        return cursor == 0 or template[cursor - 1] in " \t\r\n;|&(<>)"

    def begins_compound_assignment(start: int) -> bool:
        if start == 0 or template[start - 1] != "=":
            return False
        name_end = start - 1
        if name_end > 0 and template[name_end - 1] == "+":
            name_end -= 1
        return assignment_name_before(name_end)

    def assignment_word(start: int, end: int) -> bool:
        cursor = start
        if cursor >= end or not (
            template[cursor].isascii()
            and (template[cursor].isalpha() or template[cursor] == "_")
        ):
            return False
        cursor += 1
        while cursor < end and (
            template[cursor].isascii()
            and (template[cursor].isalnum() or template[cursor] == "_")
        ):
            cursor += 1
        if cursor < end and template[cursor] == "[":
            bracket_depth = 1
            cursor += 1
            while cursor < end and bracket_depth:
                if template[cursor] == "[":
                    bracket_depth += 1
                elif template[cursor] == "]":
                    bracket_depth -= 1
                cursor += 1
            if bracket_depth:
                return False
        if cursor < end and template[cursor] == "+":
            cursor += 1
        return cursor < end and template[cursor] == "="

    def finish_top_level_word(end: int) -> None:
        nonlocal top_level_assignment_builtin
        nonlocal top_level_command_position
        nonlocal top_level_word_start
        if top_level_word_start is None:
            return
        start = top_level_word_start
        top_level_word_start = None
        if not top_level_command_position:
            return
        word = template[start:end]
        if assignment_word(start, end):
            return
        if top_level_assignment_builtin:
            return
        if word in {"declare", "export", "local", "readonly", "typeset"}:
            top_level_assignment_builtin = True
            return
        if word in {
            "!",
            "do",
            "elif",
            "else",
            "for",
            "if",
            "select",
            "then",
            "time",
            "until",
            "while",
            "{",
        }:
            return
        top_level_command_position = False

    def function_declaration_name_end(start: int) -> int | None:
        """Consume Bash's `function WORD` prefix without parsing its body."""
        if not shell_word_at(start, "function"):
            return None
        cursor = start + len("function")
        if cursor >= len(template) or template[cursor] not in " \t":
            return None
        while cursor < len(template) and template[cursor] in " \t":
            cursor += 1
        name_start = cursor
        separators = " \t\r\n;|&()<>"
        while cursor < len(template) and template[cursor] not in separators:
            if template[cursor] in "'\"\\$`":
                raise BashRenderingError(
                    "bash_reference_context_unsupported",
                    "Bash reference follows an ambiguous function declaration",
                )
            cursor += 1
        if cursor == name_start:
            raise BashRenderingError(
                "bash_reference_context_unsupported",
                "Bash reference follows an ambiguous function declaration",
            )
        return cursor

    def coprocess_declaration_end(start: int) -> int | None:
        """Consume `coproc` and a possible name before a compound command."""
        if not shell_word_at(start, "coproc"):
            return None
        keyword_end = start + len("coproc")
        if keyword_end >= len(template) or template[keyword_end] not in " \t":
            return keyword_end
        cursor = keyword_end
        while cursor < len(template) and template[cursor] in " \t":
            cursor += 1
        compound_starters = (
            "case",
            "if",
            "for",
            "select",
            "while",
            "until",
            "function",
            "coproc",
            "time",
            "!",
            "{",
            "((",
            "(",
            "[[",
        )
        if any(shell_word_at(cursor, word) for word in compound_starters):
            return keyword_end
        name_start = cursor
        separators = " \t\r\n;|&()<>"
        while cursor < len(template) and template[cursor] not in separators:
            if template[cursor] in "'\"\\$`":
                raise BashRenderingError(
                    "bash_reference_context_unsupported",
                    "Bash reference follows an ambiguous coprocess declaration",
                )
            cursor += 1
        return cursor if cursor > name_start else keyword_end

    def check_nesting_bound(extra: int = 1) -> None:
        case_depth = sum(len(frame.case_states) for frame in frames)
        if len(frames) + case_depth + extra > _BASH_LEXER_MAX_NESTING:
            raise BashRenderingError(
                "bash_reference_context_unsupported",
                "Bash reference nesting exceeds its lexer bound",
            )

    def mark_current_command_word() -> None:
        if frames and frames[-1].kind == "command":
            frames[-1].command_position = False

    while position < len(template):
        if position in by_start:
            if frames:
                decisions[position] = False
            else:
                decisions[position] = quote

        character = template[position]
        if (
            not frames
            and quote is None
            and compound_assignment_depth == 0
            and top_level_word_start is None
            and character not in " \t\r\n;|&()<>"
        ):
            top_level_word_start = position
        if frames and frames[-1].kind == "ansi_c":
            if character == "\\":
                position = min(len(template), position + 2)
            elif character == "'":
                frame = frames.pop()
                quote = frame.resume_quote
                position += 1
            else:
                position += 1
            word_start = False
            continue
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
                mark_current_command_word()
            continue

        comments_allowed = not frames or frames[-1].kind in {"command", "backtick"}
        if quote is None and comments_allowed and character == "#" and word_start:
            newline = template.find("\n", position)
            end = len(template) if newline < 0 else newline
            decide_range(position, end, True)
            if not frames and top_level_word_start == position:
                top_level_word_start = None
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
            mark_current_command_word()
            quote = "'"
            position += 1
            word_start = False
            continue
        if character == '"':
            if quote is None:
                mark_current_command_word()
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
                mark_current_command_word()
                frames.append(_NestingFrame("backtick", quote))
                quote = None
                word_start = True
            position += 1
            continue

        if template.startswith("$$", position):
            second_dollar = position + 1
            if second_dollar in by_start:
                decisions[second_dollar] = True
            mark_current_command_word()
            position += 2
            word_start = False
            continue

        if character == "$" and position + 1 < len(template):
            if template.startswith("$((", position):
                kind = "arithmetic"
                width = 3
            elif template.startswith("$(", position):
                kind = "command"
                width = 2
            elif template.startswith("$[", position):
                kind = "legacy_arithmetic"
                width = 2
            elif quote is None and template.startswith("$'", position):
                kind = "ansi_c"
                width = 2
            elif template.startswith("${", position):
                kind = "parameter"
                width = 2
            else:
                kind = ""
                width = 0
            if kind:
                check_nesting_bound()
                mark_current_command_word()
                frames.append(_NestingFrame(kind, quote))
                quote = None
                position += width
                word_start = kind == "command"
                continue

        if quote is None and not frames and template.startswith("((", position):
            check_nesting_bound()
            frames.append(_NestingFrame("arithmetic", quote))
            position += 2
            word_start = False
            continue

        if (
            quote is None
            and shell_word_at(position, "[[")
            and (
                (not frames and top_level_command_position)
                or (
                    frames
                    and frames[-1].kind == "command"
                    and frames[-1].command_position
                    and (
                        not frames[-1].case_states
                        or frames[-1].case_states[-1] == "body"
                    )
                )
            )
        ):
            check_nesting_bound()
            mark_current_command_word()
            frames.append(_NestingFrame("conditional", quote))
            quote = None
            position += 2
            word_start = True
            continue

        heredoc_cursor = (
            begin_heredoc(position)
            if quote is None and (not frames or frames[-1].kind == "command")
            else None
        )
        if heredoc_cursor is not None:
            position = heredoc_cursor
            word_start = False
            continue

        closed_compound_assignment = False
        if quote is None and not frames:
            if array_subscript_start is not None:
                if character == "[":
                    array_subscript_depth += 1
                elif character == "]":
                    array_subscript_depth -= 1
                    if array_subscript_depth == 0:
                        suffix = template[position + 1 : position + 3]
                        if suffix.startswith("=") or suffix.startswith("+="):
                            unsupported_range(array_subscript_start, position + 1)
                        array_subscript_start = None
            elif character == "[" and (
                (compound_assignment_depth > 0 and word_start)
                or (top_level_command_position and assignment_name_before(position))
            ):
                array_subscript_start = position
                array_subscript_depth = 1

            if array_subscript_start is None:
                if (
                    character == "("
                    and top_level_command_position
                    and begins_compound_assignment(position)
                ):
                    compound_assignment_depth = 1
                elif compound_assignment_depth > 0:
                    if character == "(":
                        compound_assignment_depth += 1
                    elif character == ")":
                        compound_assignment_depth -= 1
                        closed_compound_assignment = compound_assignment_depth == 0

        if frames and quote is None:
            frame = frames[-1]
            if frame.kind == "conditional":
                if shell_word_at(position, "]]"):
                    frames.pop()
                    quote = frame.resume_quote
                    if frames and frames[-1].kind == "command":
                        frames[-1].command_position = False
                    position += 2
                    word_start = False
                    continue
                position += 1
                word_start = character in " \t\r\n"
                continue
            if frame.kind == "parameter" and character == "}":
                frames.pop()
                quote = frame.resume_quote
                position += 1
                continue
            if frame.kind == "command":
                case_state = frame.case_states[-1] if frame.case_states else None
                coprocess_end = (
                    coprocess_declaration_end(position)
                    if frame.command_position and case_state in {None, "body"}
                    else None
                )
                if coprocess_end is not None:
                    frame.command_position = True
                    word_start = True
                    position = coprocess_end
                    continue
                function_name_end = (
                    function_declaration_name_end(position)
                    if frame.command_position and case_state in {None, "body"}
                    else None
                )
                if function_name_end is not None:
                    frame.command_position = True
                    word_start = True
                    position = function_name_end
                    continue
                command_prefix = next(
                    (
                        word
                        for word in (
                            "while",
                            "until",
                            "select",
                            "then",
                            "else",
                            "elif",
                            "time",
                            "for",
                            "if",
                            "do",
                            "-p",
                            "!",
                            "{",
                        )
                        if frame.command_position
                        and case_state in {None, "body"}
                        and shell_word_at(position, word)
                    ),
                    None,
                )
                if command_prefix is not None:
                    word_start = False
                    position += len(command_prefix)
                    continue
                terminator = next(
                    (
                        token
                        for token in (";;&", ";;", ";&")
                        if case_state == "body" and template.startswith(token, position)
                    ),
                    None,
                )
                if terminator is not None:
                    frame.case_states[-1] = "pattern"
                    frame.command_position = True
                    word_start = True
                    position += len(terminator)
                    continue
                if (
                    case_state in {"pattern", "body"}
                    and frame.command_position
                    and shell_word_at(position, "esac")
                ):
                    frame.case_states.pop()
                    frame.command_position = False
                    word_start = False
                    position += len("esac")
                    continue
                if case_state == "word" and shell_word_at(position, "in"):
                    frame.case_states[-1] = "pattern"
                    frame.command_position = True
                    word_start = False
                    position += len("in")
                    continue
                if (
                    frame.command_position
                    and case_state in {None, "body"}
                    and shell_word_at(position, "case")
                ):
                    check_nesting_bound()
                    frame.case_states.append("word")
                    frame.command_position = False
                    word_start = False
                    position += len("case")
                    continue
                closed = False
                if character == ")" and frame.case_states:
                    if frame.case_states[-1] == "pattern":
                        frame.case_states[-1] = "body"
                        frame.command_position = True
                        word_start = True
                        position += 1
                        continue
                elif character == "(" and not frame.case_states:
                    frame.parenthesis_depth += 1
                    frame.command_position = True
                elif character == ")" and not frame.case_states:
                    frame.parenthesis_depth -= 1
                    if frame.parenthesis_depth == 0:
                        frames.pop()
                        quote = frame.resume_quote
                        closed = True
                if closed:
                    word_start = False
                elif character == ")" and not frame.case_states:
                    # A nested pair can be a POSIX function declaration
                    # (`name() { ...; }`).  The following command group must
                    # remain in command position so its `case` body is parsed.
                    word_start = True
                    frame.command_position = True
                elif character in " \t\r<>":
                    word_start = True
                elif character in "\n;|&":
                    word_start = True
                    frame.command_position = True
                else:
                    word_start = False
                    frame.command_position = False
                position += 1
                if character == "\n" and frame.pending_heredocs:
                    position = consume_heredocs(
                        position,
                        frame.pending_heredocs,
                    )
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
            if frame.kind == "legacy_arithmetic":
                if character == "[":
                    frame.bracket_depth += 1
                elif character == "]":
                    frame.bracket_depth -= 1
                    if frame.bracket_depth == 0:
                        frames.pop()
                        quote = frame.resume_quote
                position += 1
                continue

        if (
            quote is None
            and not frames
            and compound_assignment_depth == 0
            and character in " \t\r\n;|&()<>"
        ):
            finish_top_level_word(
                position + 1 if closed_compound_assignment else position
            )
            if character in "\n;|&" or (
                character in "()" and not closed_compound_assignment
            ):
                top_level_command_position = True
                top_level_assignment_builtin = False

        if character == "\n":
            position += 1
            word_start = True
            if top_level_heredocs:
                position = consume_heredocs(position, top_level_heredocs)
            continue
        if character in " \t\r;|&()<>" and quote is None:
            word_start = True
        else:
            word_start = False
        position += 1

    if ordered and (quote is not None or frames or top_level_heredocs):
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


def bash_output_references(template: str):
    """Parse outputs after validating every substituted Bash reference context."""
    # Local import preserves bash_rendering's dependency-neutral lexer surface.
    from plugins.workflow.language_schema import (
        iter_output_reference_candidate_spans,
        iter_output_references_in_spans,
    )

    output_candidates = tuple(
        iter_output_reference_candidate_spans(template, normalizer_version=3)
    )
    scalar_candidates: list[tuple[int, int]] = []
    output_cursor = 0
    for match in _BASH_SCALAR_REFERENCE.finditer(template):
        if (
            match.group("position") is None
            and match.group("name") not in _BASH_SCALAR_NAMES
        ):
            continue
        start, end = match.span()
        while (
            output_cursor < len(output_candidates)
            and output_candidates[output_cursor][1] <= start
        ):
            output_cursor += 1
        if output_cursor < len(output_candidates):
            output_start, output_end = output_candidates[output_cursor]
            if start < output_end and end > output_start:
                continue
        scalar_candidates.append((start, end))
    candidates = tuple(sorted((*output_candidates, *scalar_candidates)))
    output_candidate_set = frozenset(output_candidates)
    admitted = tuple(
        (start, end)
        for start, end, _quote in classify_bash_reference_spans(
            template,
            candidates,
        )
        if (start, end) in output_candidate_set
    )
    return tuple(
        iter_output_references_in_spans(
            template,
            admitted,
            normalizer_version=3,
        )
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


def _verified_spill_snapshot(
    directory_descriptor: int,
    filename: str,
    data: bytes,
    digest: str,
) -> bytes:
    create_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    write_descriptor: int | None = None
    read_descriptor: int | None = None
    created = False
    try:
        write_descriptor = os.open(
            filename,
            create_flags,
            0o600,
            dir_fd=directory_descriptor,
        )
        created = True
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
        snapshot = bytearray()
        while remaining:
            chunk = os.read(read_descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise OSError("Bash spill ended before its verified size")
            observed.update(chunk)
            snapshot.extend(chunk)
            remaining -= len(chunk)
        if os.read(read_descriptor, 1) or observed.hexdigest() != digest:
            raise OSError("Bash spill content failed verification")
        return bytes(snapshot)
    finally:
        if read_descriptor is not None:
            try:
                os.close(read_descriptor)
            except OSError:
                pass
        if write_descriptor is not None:
            try:
                os.close(write_descriptor)
            except OSError:
                pass
        if created:
            # The verified snapshot is already detached in bounded memory.
            # Unlinking here removes future pathname authority; a writer that
            # opened before this syscall can mutate only the orphaned inode.
            os.unlink(filename, dir_fd=directory_descriptor)


def _materialize_spills(
    spill_directory: Path,
    spills: tuple[tuple[bytes, str], ...],
) -> _SpillTransport:
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
    snapshots: list[bytes] = []
    transport: _SpillTransport | None = None
    directory_created = False
    try:
        os.mkdir(spill_directory.name, 0o700, dir_fd=parent_descriptor)
        directory_created = True
        directory_descriptor = os.open(
            spill_directory.name,
            _open_flags(directory=True),
            dir_fd=parent_descriptor,
        )
        directory_stat = os.fstat(directory_descriptor)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise OSError("Bash spill root is not a directory")
        for index, (data, digest) in enumerate(spills):
            snapshots.append(
                _verified_spill_snapshot(
                    directory_descriptor,
                    f"spill-{index:04d}",
                    data,
                    digest,
                )
            )
        transport = _SpillTransport.from_snapshots(tuple(snapshots))
        os.rmdir(spill_directory.name, dir_fd=parent_descriptor)
        directory_created = False
        return transport
    except BaseException:
        if transport is not None:
            transport.close()
        if directory_created:
            try:
                os.rmdir(spill_directory.name, dir_fd=parent_descriptor)
            except OSError:
                pass
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
        transport = (
            _materialize_spills(
                Path(spill_directory),
                tuple(spill_values),
            )
            if spill_values
            else None
        )
    except (OSError, NotImplementedError) as exc:
        raise BashRenderingError(
            "bash_spill_integrity",
            "Bash spill descriptor materialization failed integrity checks",
        ) from exc
    descriptors = transport.read_descriptors if transport is not None else ()
    descriptor_identities = (
        transport.read_descriptor_identities if transport is not None else ()
    )
    try:
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
            inherited_descriptor_identities=descriptor_identities,
            template_sha256=hashlib.sha256(template_bytes).hexdigest(),
            template_size_bytes=len(template_bytes),
            rendered_sha256=hashlib.sha256(rendered_bytes).hexdigest(),
            rendered_size_bytes=len(rendered_bytes),
            spill_count=len(spill_values),
            spill_total_bytes=spill_total_bytes,
            spill_content_sha256=spill_digests,
            descriptor_manifest=tuple(zip(descriptors, spill_digests, strict=True)),
            _transport=transport,
        )
    except BaseException:
        if transport is not None:
            transport.close()
        raise


__all__ = [
    "BASH_INLINE_MAX_BYTES",
    "BASH_SPILL_MAX_FILES",
    "BASH_SPILL_MAX_TOTAL_BYTES",
    "BASH_SPILL_MAX_VALUE_BYTES",
    "BashRenderingError",
    "RenderedBashCommand",
    "bash_output_references",
    "classify_bash_reference_spans",
    "render_v3_bash",
]
