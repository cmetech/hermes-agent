#!/usr/bin/env python3
"""Add a contributor email → GitHub login mapping.

Writes one file per email under contributors/emails/ (filename = email,
content = login). File additions never merge-conflict, unlike the legacy
AUTHOR_MAP dict in scripts/release.py, which is frozen — do not append to it.

Usage (from the repo root):
    python3 scripts/add_contributor.py <email> <github-login> [comment...]

    # e.g.
    python3 scripts/add_contributor.py jane@example.com janedoe "PR #12345 salvage"

Idempotent: if the mapping already exists with the same login, prints
"present" and exits 0. If the email maps to a DIFFERENT login (here or in the
legacy AUTHOR_MAP), refuses with exit 1 so a typo can't silently reassign
someone's commits.

If the email differs only in case from one already mapped, the two filenames
would collide on NTFS/APFS. Both entries move to
contributors/case-collisions.tsv instead, which stores the exact emails as
tab-delimited rows -- see that file's header.
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EMAILS_DIR = REPO_ROOT / "contributors" / "emails"
COLLISIONS_FILE = REPO_ROOT / "contributors" / "case-collisions.tsv"

_EMAIL_RE = re.compile(r"^[^/\\\s]+@[^/\\\s]+$")
# GitHub's *current* signup rules forbid consecutive hyphens, but legacy
# accounts with them exist and are valid (e.g. Roger--Han, verified via the
# users API July 2026). Accept any alphanumeric/hyphen login that doesn't
# start or end with a hyphen, max 39 chars.
_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


def read_mapping_file(path: Path) -> str | None:
    """Return the login from a mapping file (first non-comment line)."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    except OSError:
        pass
    return None


def _legacy_login(email: str) -> str | None:
    """Look the email up in the frozen legacy AUTHOR_MAP in release.py."""
    try:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from release import LEGACY_AUTHOR_MAP  # noqa: PLC0415

        return LEGACY_AUTHOR_MAP.get(email)
    except Exception:
        return None


def read_collisions() -> dict:
    """Parse contributors/case-collisions.tsv into {exact email: login}."""
    mapping = {}
    try:
        text = COLLISIONS_FILE.read_text(encoding="utf-8")
    except OSError:
        return mapping
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        email, _, login = line.partition("\t")
        # A row may carry a trailing "# note"; the login itself never can.
        login = login.partition("#")[0]
        email, login = email.strip(), login.strip().lstrip("@")
        if email and login:
            mapping[email] = login
    return mapping


def _append_collision(email: str, login: str, comment: str = "") -> None:
    """Append one exact-email row to contributors/case-collisions.tsv."""
    COLLISIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = COLLISIONS_FILE.read_text(encoding="utf-8") if COLLISIONS_FILE.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    row = f"{email}\t{login}"
    if comment:
        row += f"  # {comment}"
    COLLISIONS_FILE.write_text(existing + row + "\n", encoding="utf-8", newline="\n")


def _case_variant(email: str) -> str | None:
    """Return an existing mapping filename differing from email only in case.

    Compares against the real directory listing: on a case-insensitive
    filesystem ``(EMAILS_DIR / email).is_file()`` happily matches a
    differently-cased file, which is exactly the confusion guarded against
    here.
    """
    try:
        names = [entry.name for entry in EMAILS_DIR.iterdir() if entry.is_file()]
    except OSError:
        return None
    lowered = email.lower()
    for name in names:
        if name != email and name.lower() == lowered:
            return name
    return None


def add_contributor(email: str, login: str, comment: str = "") -> int:
    email = email.strip()
    login = login.strip().lstrip("@")

    if not _EMAIL_RE.match(email):
        print(f"error: {email!r} does not look like a commit-author email", file=sys.stderr)
        return 2
    if not _LOGIN_RE.match(login):
        print(f"error: {login!r} is not a valid GitHub login", file=sys.stderr)
        return 2

    path = EMAILS_DIR / email
    collisions = read_collisions()
    variant = _case_variant(email)

    # Exact-match mappings, in precedence order: collisions file, directory
    # file, frozen legacy map. `path.is_file()` is itself case-insensitive on
    # NTFS/APFS, so only trust it when no case variant shadows it.
    existing = collisions.get(email)
    if existing is None and variant is None and path.is_file():
        existing = read_mapping_file(path)
    if existing is None:
        existing = _legacy_login(email)
    if existing is not None:
        if existing == login:
            print("present")
            return 0
        print(
            f"error: {email} already maps to {existing!r} (asked for {login!r}) — "
            "resolve manually",
            file=sys.stderr,
        )
        return 1

    if variant is not None:
        # Writing contributors/emails/<email> here would create two filenames
        # differing only in case, which NTFS and APFS cannot both check out.
        # Move the pair into the tab-delimited collisions file, which holds
        # both exact spellings. Distinct people do land here — agent@Agents-
        # Mac-mini.local and agent@agents-Mac-mini.local are two real GitHub
        # users — so the entries are kept apart rather than merged.
        variant_path = EMAILS_DIR / variant
        variant_login = collisions.get(variant) or read_mapping_file(variant_path)
        if variant not in collisions and variant_login:
            _append_collision(variant, variant_login, "moved from contributors/emails/")
            variant_path.unlink()
        _append_collision(email, login, comment)
        print(f"added: contributors/case-collisions.tsv {email} -> {login}")
        print(f"note: collides with {variant} (case only), which maps to "
              f"{variant_login}; both now live in the collisions file")
        return 0

    EMAILS_DIR.mkdir(parents=True, exist_ok=True)
    body = login + "\n"
    if comment:
        body += f"# {comment}\n"
    path.write_text(body, encoding="utf-8")
    print(f"added: contributors/emails/{email} -> {login}")
    return 0


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    email, login = sys.argv[1], sys.argv[2]
    comment = " ".join(sys.argv[3:])
    return add_contributor(email, login, comment)


if __name__ == "__main__":
    sys.exit(main())
