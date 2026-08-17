"""AQL query preparation.

Raw AQL is exposed deliberately: it is the whole value of Artifactory
search, and the configured token carries the user's own permissions, so a
query cannot reach content the user could not already read. What the
connector adds is bounds and one non-obvious permission rule that
Artifactory enforces but does not advertise.
"""

from __future__ import annotations

import re

if __package__:
    from .models import ArmError
else:
    from models import ArmError


_MAX_QUERY_CHARS = 8192

# A domain call is what makes a string AQL. The domain itself may be dotted
# (archive.entries), so the pattern allows it rather than enumerating the
# domains JFrog happens to ship this version.
_DOMAIN_FIND = re.compile(
    r"^\s*[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*\s*\.\s*find\s*\("
)
_MODIFIER_START = re.compile(r"\.\s*(include|limit)\s*\(")
_QUOTED_FIELD = re.compile(r"""['\"]([^'\"]+)['\"]""")

# Artifactory rejects an include that omits any of these:
#   "For permissions reasons AQL demands the following fields:
#    repo, path and name."
# Documented at oscar_app/oscar/utils/cleanup_artifactory_releases.sh:174-178.
REQUIRED_FIELDS = ("repo", "path", "name")

# Used when the caller supplies no include at all. Without one Artifactory
# returns roughly forty columns per row.
DEFAULT_FIELDS = ("repo", "path", "name", "size", "created", "modified")


def _render(fields) -> str:
    return ".include(" + ",".join(f'"{field}"' for field in fields) + ")"


def _call_end(text: str, opening: int) -> int | None:
    """Return the index after a modifier call's closing parenthesis."""
    depth = 1
    quote: str | None = None
    index = opening + 1
    while index < len(text):
        character = text[index]
        if quote is not None:
            if character == "\\":
                index += 2
                continue
            if character == quote:
                quote = None
        elif character in ("'", '"'):
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _top_level_modifiers(text: str) -> list[tuple[str, int, int, int | None]]:
    """Find AQL include/limit calls outside quoted values and nested input.

    AQL predicates are JSON-like and may legitimately contain modifier-looking
    text. This deliberately tracks only quoting, escaping, and nesting; it is
    not an AQL parser.
    """
    modifiers: list[tuple[str, int, int, int | None]] = []
    depth = 0
    quote: str | None = None
    index = 0
    while index < len(text):
        character = text[index]
        if quote is not None:
            if character == "\\":
                index += 2
                continue
            if character == quote:
                quote = None
        elif character in ("'", '"'):
            quote = character
        elif character in "([{":
            depth += 1
        elif character in ")]}":
            depth = max(depth - 1, 0)
        elif character == "." and depth == 0:
            match = _MODIFIER_START.match(text, index)
            if match is not None:
                opening = match.end() - 1
                end = _call_end(text, opening)
                modifiers.append((match.group(1), index, opening, end))
                index = end if end is not None else match.end()
                continue
        index += 1
    return modifiers


def prepare(query: str, *, max_results: int) -> str:
    """Validate an AQL query and return it with bounds and permission fields.

    Adding fields to an include changes which columns come back, never which
    rows match. Appending the limit is the connector-enforced result bound.
    """
    if type(max_results) is not int or not 1 <= max_results <= 100:
        raise ArmError("invalid_input")
    if not isinstance(query, str):
        raise ArmError("invalid_input")
    text = query.strip()
    if not text or len(text) > _MAX_QUERY_CHARS:
        raise ArmError(
            "invalid_input",
            remediation=f"AQL query must be 1 to {_MAX_QUERY_CHARS} characters.",
        )
    if _DOMAIN_FIND.match(text) is None:
        raise ArmError(
            "invalid_input",
            remediation=(
                "AQL must begin with a domain find call, for example "
                'items.find({"repo":"generic-local"}).'
            ),
        )
    modifiers = _top_level_modifiers(text)
    if any(name == "limit" for name, _start, _opening, _end in modifiers):
        raise ArmError(
            "invalid_input",
            remediation=(
                "Do not put .limit() in the query; AQL accepts only one and "
                "the connector supplies it. Use max_results instead."
            ),
        )

    include = next(
        (
            modifier
            for modifier in modifiers
            if modifier[0] == "include" and modifier[3] is not None
        ),
        None,
    )
    if include is None:
        text = f"{text}{_render(DEFAULT_FIELDS)}"
    else:
        _name, start, opening, end = include
        assert end is not None
        present = _QUOTED_FIELD.findall(text[opening + 1:end - 1])
        missing = [field for field in REQUIRED_FIELDS if field not in present]
        if missing:
            text = (
                text[:start]
                + _render([*present, *missing])
                + text[end:]
            )

    return f"{text}.limit({max_results})"
