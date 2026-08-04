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

from plugins.workflow.language_schema import (
    BASH_INLINE_MAX_BYTES,
    BASH_RENDERED_COMMAND_MAX_BYTES,
    BASH_SPILL_MAX_FILES,
    BASH_SPILL_MAX_TOTAL_BYTES,
    BASH_SPILL_MAX_VALUE_BYTES,
)


if TYPE_CHECKING:
    from tools.managed_process import InheritedDescriptorIdentity


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
    pending_heredocs: list[tuple[str, bool, bool]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _DequotedBashWord:
    text: str
    source_starts: tuple[int, ...]
    source_ends: tuple[int, ...]


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


def _logical_token_match(
    template: str,
    start: int,
    token: str,
) -> tuple[int, tuple[int, ...]] | None:
    """Match one fixed shell token across active physical continuations."""
    cursor = start
    physical_starts: list[int] = []
    for expected in token:
        while template.startswith("\\\n", cursor):
            cursor += 2
        if cursor >= len(template) or template[cursor] != expected:
            return None
        physical_starts.append(cursor)
        cursor += 1
    return cursor, tuple(physical_starts)


def _skip_bash_continuations(template: str, start: int) -> int:
    cursor = start
    while template.startswith("\\\n", cursor):
        cursor += 2
    return cursor


def _parse_physical_bash_heredoc_delimiter(
    template: str,
    start: int,
) -> tuple[int, int, str, bool, bool] | None:
    """Parse one context-approved heredoc from authored source bytes."""
    operator = _logical_token_match(template, start, "<<")
    if operator is None or _logical_token_match(template, start, "<<<") is not None:
        return None
    cursor = _skip_bash_continuations(template, operator[0])
    strip_tabs = cursor < len(template) and template[cursor] == "-"
    if strip_tabs and cursor < len(template) and template[cursor] == "-":
        cursor = _skip_bash_continuations(template, cursor + 1)
    while cursor < len(template) and template[cursor] in " \t":
        cursor += 1
    delimiter_start = cursor
    delimiter: list[str] = []
    delimiter_quote: str | None = None
    delimiter_quoted = False
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
            delimiter_quoted = True
            if delimiter_quote == '"' and following not in '$`"\\':
                delimiter.extend(("\\", following))
            else:
                delimiter.append(following)
            cursor += 2
            continue
        if item == "'" and delimiter_quote != '"':
            delimiter_quoted = True
            delimiter_quote = None if delimiter_quote == "'" else "'"
        elif item == '"' and delimiter_quote != "'":
            delimiter_quoted = True
            delimiter_quote = None if delimiter_quote == '"' else '"'
        else:
            delimiter.append(item)
        cursor += 1
    if not delimiter or delimiter_quote is not None:
        raise BashRenderingError(
            "bash_reference_context_unsupported",
            "Bash reference appears near an ambiguous here-document delimiter",
        )
    return cursor, delimiter_start, "".join(delimiter), strip_tabs, delimiter_quoted


def _quote_removed_bash_word(template: str, start: int, end: int) -> _DequotedBashWord:
    """Remove shell word quotes while retaining scanner-source coordinates."""
    text: list[str] = []
    source_starts: list[int] = []
    source_ends: list[int] = []
    quote: str | None = None
    cursor = start

    def append(character: str, physical_start: int, physical_end: int) -> None:
        text.append(character)
        source_starts.append(physical_start)
        source_ends.append(physical_end)

    while cursor < end:
        character = template[cursor]
        if quote == "'":
            if character == "'":
                quote = None
            else:
                append(character, cursor, cursor + 1)
            cursor += 1
            continue
        if character == "'" and quote is None:
            quote = "'"
            cursor += 1
            continue
        if character == '"':
            quote = None if quote == '"' else '"'
            cursor += 1
            continue
        if character == "\\" and cursor + 1 < end:
            following = template[cursor + 1]
            if following == "\n" and quote != "'":
                cursor += 2
                continue
            if quote != '"' or following in '$`"\\':
                append(following, cursor, cursor + 2)
                cursor += 2
                continue
        append(character, cursor, cursor + 1)
        cursor += 1

    if quote is not None:
        raise BashRenderingError(
            "bash_reference_context_unsupported",
            "Bash reference appears in an ambiguous shell word",
        )
    return _DequotedBashWord(
        "".join(text),
        tuple(source_starts),
        tuple(source_ends),
    )


def _assignment_subscript_bounds(word: str) -> tuple[int, int] | None:
    cursor = 0
    if not word or not (word[0].isascii() and (word[0].isalpha() or word[0] == "_")):
        return None
    cursor += 1
    while cursor < len(word) and (
        word[cursor].isascii() and (word[cursor].isalnum() or word[cursor] == "_")
    ):
        cursor += 1
    if cursor >= len(word) or word[cursor] != "[":
        return None
    bracket_start = cursor
    bracket_depth = 1
    cursor += 1
    while cursor < len(word) and bracket_depth:
        if word[cursor] == "[":
            bracket_depth += 1
        elif word[cursor] == "]":
            bracket_depth -= 1
        cursor += 1
    if bracket_depth:
        return None
    bracket_end = cursor
    if cursor < len(word) and word[cursor] == "+":
        cursor += 1
    if cursor >= len(word) or word[cursor] != "=":
        return None
    return bracket_start, bracket_end


def classify_bash_reference_spans(
    template: str,
    spans: Iterable[tuple[int, int]],
) -> tuple[tuple[int, int, str | None], ...]:
    """Return admitted physical spans from one authored-source shell scan."""
    ordered = _validate_bash_reference_spans(template, spans)
    return _classify_authored_bash_reference_spans(template, ordered)


def _classify_authored_bash_reference_spans(
    template: str,
    spans: Iterable[tuple[int, int]],
) -> tuple[tuple[int, int, str | None], ...]:
    """Classify spans while preserving authored coordinates and lexer phase."""
    ordered = _validate_bash_reference_spans(template, spans)
    by_start = {start: (start, end) for start, end in ordered}
    if len(by_start) != len(ordered):
        raise ValueError("bash substitution offsets must be unique")
    reference_starts = tuple(by_start)
    decisions: dict[int, str | None | bool] = {}
    quote: str | None = None
    frames: list[_NestingFrame] = []
    top_level_heredocs: list[tuple[str, bool, bool]] = []
    word_start = True
    position = 0
    compound_assignment_depth = 0
    array_subscript_start: int | None = None
    array_subscript_depth = 0
    top_level_command_position = True
    top_level_assignment_builtin = False
    top_level_arithmetic_builtin: str | None = None
    top_level_integer_attribute = False
    top_level_command_wrapper: str | None = None
    top_level_redirection_operand = False
    top_level_word_start: int | None = None

    def decide_range(start: int, end: int, decision: bool) -> None:
        first = bisect_left(reference_starts, start)
        last = bisect_left(reference_starts, end, lo=first)
        for reference_start in reference_starts[first:last]:
            if decision is False and decisions.get(reference_start) is True:
                continue
            decisions[reference_start] = decision

    def unsupported_range(start: int, end: int) -> None:
        decide_range(start, end, False)

    def consume_heredocs(
        start: int,
        pending_heredocs: list[tuple[str, bool, bool]],
    ) -> int:
        cursor = start
        for delimiter, strip_tabs, delimiter_quoted in pending_heredocs:
            body_start = cursor
            while cursor <= len(template):
                line_parts: list[str] = []
                line_end = cursor
                while True:
                    newline = template.find("\n", line_end)
                    physical_end = len(template) if newline < 0 else newline
                    part = template[line_end:physical_end]
                    trailing_backslashes = len(part) - len(part.rstrip("\\"))
                    if (
                        not delimiter_quoted
                        and newline >= 0
                        and trailing_backslashes % 2 == 1
                    ):
                        line_parts.append(part[:-1])
                        line_end = newline + 1
                        continue
                    line_parts.append(part)
                    line_end = physical_end
                    break
                line = "".join(line_parts)
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
        parsed = _parse_physical_bash_heredoc_delimiter(template, start)
        if parsed is None:
            return None
        cursor, delimiter_start, delimiter, strip_tabs, delimiter_quoted = parsed
        unsupported_range(delimiter_start, cursor)
        pending_heredocs = (
            frames[-1].pending_heredocs
            if frames and frames[-1].kind in {"command", "backtick"}
            else top_level_heredocs
        )
        pending_heredocs.append((delimiter, strip_tabs, delimiter_quoted))
        return cursor

    def shell_word_end(start: int, word: str) -> int | None:
        if start >= len(template) or template[start] != word[0]:
            return None
        match = _logical_token_match(template, start, word)
        if match is None:
            return None
        before_cursor = start
        while before_cursor >= 2 and template.startswith(
            "\\\n",
            before_cursor - 2,
        ):
            before_cursor -= 2
        before = template[before_cursor - 1] if before_cursor else " "
        end = _skip_bash_continuations(template, match[0])
        after = template[end] if end < len(template) else " "
        separators = " \t\r\n;|&()<>"
        return end if before in separators and after in separators else None

    def brace_expansion_end(start: int) -> int | None:
        """Return the authored end of a brace word that can multiply words."""
        cursor = start + 1
        depth = 1
        nested_quote: str | None = None
        has_separator = False
        while cursor < len(template):
            character = template[cursor]
            if nested_quote == "'":
                if character == "'":
                    nested_quote = None
                cursor += 1
                continue
            if character == "\\":
                cursor = min(len(template), cursor + 2)
                continue
            if character == "'":
                nested_quote = "'"
                cursor += 1
                continue
            if character == '"':
                nested_quote = None if nested_quote == '"' else '"'
                cursor += 1
                continue
            if nested_quote is not None:
                cursor += 1
                continue
            if character in " \t\r\n;|&<>()":
                return None
            if character == "{":
                depth += 1
                if depth > _BASH_LEXER_MAX_NESTING:
                    return None
            elif character == "}":
                depth -= 1
                if depth == 0:
                    return cursor + 1 if has_separator else None
            elif character == "," or template.startswith("..", cursor):
                has_separator = True
            cursor += 1
        return None

    def assignment_name_before(end: int, *, allow_quote_boundary: bool = False) -> bool:
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
        return (
            cursor == 0
            or template[cursor - 1] in " \t\r\n;|&(<>)"
            or (allow_quote_boundary and template[cursor - 1] in "'\"")
        )

    def begins_compound_assignment(start: int) -> bool:
        if start == 0 or template[start - 1] != "=":
            return False
        name_end = start - 1
        if name_end > 0 and template[name_end - 1] == "+":
            name_end -= 1
        return assignment_name_before(name_end)

    def assignment_word(word: str) -> bool:
        cursor = 0
        if not word or not (
            word[cursor].isascii() and (word[cursor].isalpha() or word[cursor] == "_")
        ):
            return False
        cursor += 1
        while cursor < len(word) and (
            word[cursor].isascii() and (word[cursor].isalnum() or word[cursor] == "_")
        ):
            cursor += 1
        if cursor < len(word) and word[cursor] == "[":
            bracket_depth = 1
            cursor += 1
            while cursor < len(word) and bracket_depth:
                if word[cursor] == "[":
                    bracket_depth += 1
                elif word[cursor] == "]":
                    bracket_depth -= 1
                cursor += 1
            if bracket_depth:
                return False
        if cursor < len(word) and word[cursor] == "+":
            cursor += 1
        return cursor < len(word) and word[cursor] == "="

    def finish_top_level_word(end: int) -> None:
        nonlocal top_level_assignment_builtin
        nonlocal top_level_arithmetic_builtin
        nonlocal top_level_command_position
        nonlocal top_level_command_wrapper
        nonlocal top_level_integer_attribute
        nonlocal top_level_redirection_operand
        nonlocal top_level_word_start
        if top_level_word_start is None:
            return
        start = top_level_word_start
        top_level_word_start = None
        if top_level_redirection_operand:
            top_level_redirection_operand = False
            return
        if not top_level_command_position:
            return
        dequoted = _quote_removed_bash_word(template, start, end)
        word = dequoted.text
        subscript_bounds = _assignment_subscript_bounds(word)
        if subscript_bounds is not None and (
            top_level_command_position or top_level_assignment_builtin
        ):
            bracket_start, bracket_end = subscript_bounds
            unsupported_range(
                dequoted.source_starts[bracket_start],
                dequoted.source_ends[bracket_end - 1],
            )
        if top_level_arithmetic_builtin == "let":
            unsupported_range(start, end)
            return
        if top_level_assignment_builtin:
            is_assignment = assignment_word(word)
            if not is_assignment and word == "--":
                return
            if (
                not is_assignment
                and len(word) > 1
                and word[0] in {"-", "+"}
                and word[1:].isalpha()
            ):
                if "i" in word[1:]:
                    top_level_integer_attribute = word[0] == "-"
                return
            if top_level_integer_attribute:
                unsupported_range(start, end)
            return
        if assignment_word(word):
            return
        if word in {"builtin", "command"}:
            top_level_command_wrapper = word
            return
        if top_level_command_wrapper == "command" and word in {"-p", "--"}:
            return
        if top_level_command_wrapper == "builtin" and word == "--":
            return
        top_level_command_wrapper = None
        if word == "let":
            top_level_arithmetic_builtin = "let"
            return
        if word in {"declare", "export", "local", "readonly", "typeset"}:
            top_level_assignment_builtin = True
            top_level_integer_attribute = False
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
        keyword_end = shell_word_end(start, "function")
        if keyword_end is None:
            return None
        cursor = keyword_end
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
        keyword_end = shell_word_end(start, "coproc")
        if keyword_end is None:
            return None
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
        if any(shell_word_end(cursor, word) is not None for word in compound_starters):
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
            and top_level_command_position
            and top_level_word_start is None
        ):
            coprocess_end = coprocess_declaration_end(position)
            if coprocess_end is not None:
                position = coprocess_end
                word_start = True
                continue
            function_name_end = function_declaration_name_end(position)
            if function_name_end is not None:
                position = function_name_end
                word_start = True
                continue
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

        if not frames and top_level_assignment_builtin and quote in {"'", '"'}:
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
            elif character == "[" and assignment_name_before(
                position,
                allow_quote_boundary=True,
            ):
                array_subscript_start = position
                array_subscript_depth = 1

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

        double_dollar = (
            _logical_token_match(template, position, "$$") if character == "$" else None
        )
        if double_dollar is not None:
            second_dollar = double_dollar[1][1]
            if second_dollar in by_start:
                decisions[second_dollar] = True
            mark_current_command_word()
            position = double_dollar[0]
            word_start = False
            continue

        if character == "$" and position + 1 < len(template):
            arithmetic_open = _logical_token_match(template, position, "$((")
            command_open = _logical_token_match(template, position, "$(")
            legacy_open = _logical_token_match(template, position, "$[")
            ansi_open = _logical_token_match(template, position, "$'")
            parameter_open = _logical_token_match(template, position, "${")
            if arithmetic_open is not None:
                kind = "arithmetic"
                frame_end = arithmetic_open[0]
            elif command_open is not None:
                kind = "command"
                frame_end = command_open[0]
            elif legacy_open is not None:
                kind = "legacy_arithmetic"
                frame_end = legacy_open[0]
            elif quote is None and ansi_open is not None:
                kind = "ansi_c"
                frame_end = ansi_open[0]
            elif parameter_open is not None:
                kind = "parameter"
                frame_end = parameter_open[0]
            else:
                kind = ""
                frame_end = position
            if kind:
                check_nesting_bound()
                mark_current_command_word()
                frames.append(_NestingFrame(kind, quote))
                quote = None
                position = frame_end
                word_start = kind == "command"
                continue

        top_arithmetic_open = (
            _logical_token_match(template, position, "((") if character == "(" else None
        )
        if quote is None and not frames and top_arithmetic_open is not None:
            check_nesting_bound()
            frames.append(_NestingFrame("arithmetic", quote))
            position = top_arithmetic_open[0]
            word_start = False
            continue

        conditional_open_end = shell_word_end(position, "[[") if quote is None else None
        if (
            quote is None
            and conditional_open_end is not None
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
            position = conditional_open_end
            word_start = True
            continue

        process_substitution = (
            _logical_token_match(template, position, f"{character}(")
            if character in "<>"
            else None
        )
        if (
            quote is None
            and process_substitution is not None
            and (not frames or frames[-1].kind in {"command", "backtick"})
        ):
            check_nesting_bound()
            mark_current_command_word()
            frames.append(_NestingFrame("command", quote))
            quote = None
            position = process_substitution[0]
            word_start = True
            continue

        extglob_open = (
            _logical_token_match(template, position, f"{character}(")
            if character in "@!+?*"
            else None
        )
        if quote is None and extglob_open is not None:
            check_nesting_bound()
            mark_current_command_word()
            frames.append(_NestingFrame("extglob", quote))
            quote = None
            position = extglob_open[0]
            word_start = False
            continue

        if (
            quote is None
            and not frames
            and character == "{"
            and brace_expansion_end(position) is not None
        ):
            check_nesting_bound()
            frames.append(_NestingFrame("brace", quote))
            position += 1
            word_start = False
            continue

        here_string = (
            _logical_token_match(template, position, "<<<")
            if character == "<"
            and quote is None
            and (not frames or frames[-1].kind in {"command", "backtick"})
            else None
        )
        if here_string is not None:
            if not frames and compound_assignment_depth == 0:
                if (
                    top_level_word_start is not None
                    and template[top_level_word_start:position].isdigit()
                ):
                    top_level_word_start = None
                top_level_redirection_operand = True
            position = here_string[0]
            word_start = True
            continue

        heredoc_cursor = (
            begin_heredoc(position)
            if character == "<"
            and quote is None
            and (not frames or frames[-1].kind in {"command", "backtick"})
            else None
        )
        if heredoc_cursor is not None:
            if (
                not frames
                and compound_assignment_depth == 0
                and top_level_command_position
                and top_level_word_start is not None
                and template[top_level_word_start:position].isdigit()
            ):
                top_level_word_start = None
                top_level_redirection_operand = False
            position = heredoc_cursor
            word_start = False
            continue

        if (
            quote is None
            and not frames
            and compound_assignment_depth == 0
            and top_level_command_position
            and character in "<>"
        ):
            if (
                top_level_word_start is not None
                and template[top_level_word_start:position].isdigit()
            ):
                top_level_word_start = None
            top_level_redirection_operand = True

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
                conditional_close_end = shell_word_end(position, "]]")
                if conditional_close_end is not None:
                    frames.pop()
                    quote = frame.resume_quote
                    if frames and frames[-1].kind == "command":
                        frames[-1].command_position = False
                    position = conditional_close_end
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
            if frame.kind == "backtick":
                position += 1
                word_start = character in " \t\r\n;|&()<>"
                if character == "\n" and frame.pending_heredocs:
                    position = consume_heredocs(position, frame.pending_heredocs)
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
                command_prefix_end = next(
                    (
                        authored_end
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
                        and (authored_end := shell_word_end(position, word)) is not None
                    ),
                    None,
                )
                if command_prefix_end is not None:
                    word_start = False
                    position = command_prefix_end
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
                esac_end = (
                    shell_word_end(position, "esac")
                    if case_state in {"pattern", "body"} and frame.command_position
                    else None
                )
                if esac_end is not None:
                    frame.case_states.pop()
                    frame.command_position = False
                    word_start = False
                    position = esac_end
                    continue
                in_end = (
                    shell_word_end(position, "in") if case_state == "word" else None
                )
                if in_end is not None:
                    frame.case_states[-1] = "pattern"
                    frame.command_position = True
                    word_start = False
                    position = in_end
                    continue
                case_end = (
                    shell_word_end(position, "case")
                    if frame.command_position and case_state in {None, "body"}
                    else None
                )
                if case_end is not None:
                    check_nesting_bound()
                    frame.case_states.append("word")
                    frame.command_position = False
                    word_start = False
                    position = case_end
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
            if frame.kind == "extglob":
                if character == "(":
                    frame.parenthesis_depth += 1
                elif character == ")":
                    frame.parenthesis_depth -= 1
                    if frame.parenthesis_depth == 0:
                        frames.pop()
                        quote = frame.resume_quote
                position += 1
                continue
            if frame.kind == "brace":
                if character == "{":
                    frame.bracket_depth += 1
                elif character == "}":
                    frame.bracket_depth -= 1
                    if frame.bracket_depth == 0:
                        frames.pop()
                        quote = frame.resume_quote
                position += 1
                continue
            if frame.kind == "arithmetic":
                if character == "(":
                    frame.parenthesis_depth += 1
                elif character == ")":
                    arithmetic_close = _logical_token_match(template, position, "))")
                    if frame.parenthesis_depth == 1 and arithmetic_close is not None:
                        frames.pop()
                        quote = frame.resume_quote
                        position = arithmetic_close[0]
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
                top_level_arithmetic_builtin = None
                top_level_integer_attribute = False
                top_level_command_wrapper = None

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

    if quote is None and not frames and compound_assignment_depth == 0:
        finish_top_level_word(len(template))
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


def _compose_v3_bash_command(
    template: str,
    ordered: tuple[tuple[int, int, str, str | None], ...],
    variables: dict[bytes, str],
    descriptors: tuple[int | str, ...],
) -> str:
    prologue = "".join(
        (
            f"{variables[data]}=$(command cat <&{descriptors[index]}; "
            '__hermes_rc=$?; printf x; exit "$__hermes_rc") || exit $?\n'
            f"{variables[data]}=${{{variables[data]}%x}}\n"
        )
        for data, index in (
            (data, position)
            for position, data in enumerate(variables)
        )
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
    return "".join(rendered)


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
    variables = {
        data: f"__HERMES_WF_SPILL_{digest}"
        for data, (digest, _index) in spill_by_value.items()
    }
    projected = _compose_v3_bash_command(
        template,
        ordered,
        variables,
        tuple("9" * 20 for _value in spill_values),
    )
    if len(projected.encode("utf-8")) > BASH_RENDERED_COMMAND_MAX_BYTES:
        raise BashRenderingError(
            "bash_substitution_limit",
            "rendered Bash command exceeds its aggregate byte limit",
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
        rendered_command = _compose_v3_bash_command(
            template,
            ordered,
            variables,
            descriptors,
        )
        template_bytes = template.encode("utf-8")
        rendered_bytes = rendered_command.encode("utf-8")
        if len(rendered_bytes) > BASH_RENDERED_COMMAND_MAX_BYTES:
            raise BashRenderingError(
                "bash_substitution_limit",
                "rendered Bash command exceeds its aggregate byte limit",
            )
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
    "BASH_RENDERED_COMMAND_MAX_BYTES",
    "BASH_SPILL_MAX_FILES",
    "BASH_SPILL_MAX_TOTAL_BYTES",
    "BASH_SPILL_MAX_VALUE_BYTES",
    "BashRenderingError",
    "RenderedBashCommand",
    "bash_output_references",
    "classify_bash_reference_spans",
    "render_v3_bash",
]
