"""Tests for the conflict-free contributor mapping system.

New contributor email → GitHub login mappings live as one file per email
under contributors/emails/ (additions never merge-conflict). The legacy
AUTHOR_MAP dict in scripts/release.py is frozen; release.py merges both at
import time with the directory winning on duplicates.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))

import release  # noqa: E402
from add_contributor import add_contributor, read_mapping_file  # noqa: E402


# ── directory loader behavior ─────────────────────────────────────────


def test_loader_reads_login_from_first_noncomment_line(tmp_path):
    d = tmp_path / "emails"
    d.mkdir()
    (d / "jane@example.com").write_text("# salvage PR #1\njanedoe\n# trailing note\n")
    mapping = release._load_contributor_dir(d)
    assert mapping == {"jane@example.com": "janedoe"}






def test_effective_map_merges_legacy_and_directory():
    # Invariant: every legacy entry survives into the effective map unless
    # shadowed by a directory entry, and the directory contributes on top.
    assert set(release.LEGACY_AUTHOR_MAP) <= (
        set(release.AUTHOR_MAP) | set(release._load_contributor_dir())
    )
    for email, login in release._load_contributor_dir().items():
        assert release.AUTHOR_MAP[email] == login




# ── add_contributor.py CLI behavior ───────────────────────────────────


@pytest.fixture()
def emails_dir(tmp_path, monkeypatch):
    import add_contributor

    d = tmp_path / "contributors" / "emails"
    monkeypatch.setattr(add_contributor, "EMAILS_DIR", d)
    return d


def test_add_creates_mapping_file(emails_dir):
    rc = add_contributor("new@example.com", "newperson", "PR #999 salvage")
    assert rc == 0
    path = emails_dir / "new@example.com"
    assert path.is_file()
    assert read_mapping_file(path) == "newperson"
    assert "# PR #999 salvage" in path.read_text()






def test_add_refuses_login_conflicting_with_legacy_map(emails_dir):
    email, login = next(iter(release.LEGACY_AUTHOR_MAP.items()))
    assert add_contributor(email, login + "x") == 1
    assert not (emails_dir / email).exists()




def test_add_accepts_legacy_consecutive_hyphen_login(emails_dir):
    # Legacy GitHub accounts with consecutive hyphens are real (Roger--Han);
    # current signup rules forbid them but existing logins remain valid.
    assert add_contributor("roger.hanhong@gmail.com", "Roger--Han") == 0
    assert (emails_dir / "roger.hanhong@gmail.com").read_text(
        encoding="utf-8"
    ).strip().endswith("Roger--Han")


def test_add_strips_at_prefix(emails_dir):
    assert add_contributor("z@z.com", "@zeta") == 0
    assert read_mapping_file(emails_dir / "z@z.com") == "zeta"


def test_cli_entrypoint_end_to_end(tmp_path):
    # Run the real script in a subprocess against a temp repo layout.
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("add_contributor.py",):
        # Explicit encoding: add_contributor.py contains UTF-8 multi-byte
        # characters (an em dash), so the locale-default read_text() raises
        # UnicodeDecodeError on non-UTF-8 Windows locales (e.g. cp950).
        (scripts / name).write_text(
            (SCRIPTS_DIR / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    # Minimal stub release.py so the legacy lookup import works
    (scripts / "release.py").write_text("LEGACY_AUTHOR_MAP = {}\n")
    proc = subprocess.run(
        [sys.executable, str(scripts / "add_contributor.py"),
         "cli@example.com", "cliperson", "via subprocess"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    out = (tmp_path / "contributors" / "emails" / "cli@example.com").read_text(encoding="utf-8")
    assert out.splitlines()[0] == "cliperson"


# ── case-collision handling ───────────────────────────────────────────


def test_no_case_colliding_paths_are_tracked():
    # A clone on NTFS/APFS cannot materialize two paths differing only in
    # case: git warns and leaves one entry permanently "modified".
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True,
    ).stdout.splitlines()
    seen = {}
    for path in tracked:
        seen.setdefault(path.lower(), []).append(path)
    collisions = {k: v for k, v in seen.items() if len(v) > 1}
    assert not collisions, f"case-colliding tracked paths: {collisions}"


def test_collisions_file_parses(tmp_path):
    f = tmp_path / "case-collisions.tsv"
    f.write_text(
        "# header comment\n"
        "\n"
        "a@Host.local\tapple  # moved from contributors/emails/\n"
        "a@host.local\t@banana\n",
        encoding="utf-8",
    )
    assert release._load_contributor_collisions(f) == {
        "a@Host.local": "apple",
        "a@host.local": "banana",
    }


def test_collision_entries_reach_the_effective_map():
    collisions = release._load_contributor_collisions()
    assert collisions, "expected contributors/case-collisions.tsv to hold entries"
    for email, login in collisions.items():
        assert release.AUTHOR_MAP[email] == login
        assert release.resolve_author("ignored", email) == f"@{login}"


def test_casefold_fallback_resolves_hostname_case_drift():
    # Same machine, hostname reported with different case between commits.
    mapping = {"dev@Foo-Mac-mini.local": "devperson"}
    index = release._build_casefold_index(mapping)
    assert index["dev@foo-mac-mini.local"] == "devperson"


def test_casefold_fallback_refuses_ambiguous_groups():
    # Two distinct GitHub users whose emails differ only in case: guessing
    # would credit the wrong person, so neither is offered as a fallback.
    mapping = {"agent@A.local": "one", "agent@a.local": "two"}
    assert release._build_casefold_index(mapping) == {}


def test_lookup_prefers_exact_case_over_fallback():
    assert release.lookup_author_login("agent@Agents-Mac-mini.local") == "skip-agent"
    assert release.lookup_author_login("agent@agents-Mac-mini.local") == "momomojo"
    # Neither spelling: ambiguous group, so no guess.
    assert release.lookup_author_login("AGENT@AGENTS-MAC-MINI.LOCAL") is None


def test_add_contributor_diverts_case_variant_to_collisions_file(
    tmp_path, monkeypatch
):
    import add_contributor

    emails = tmp_path / "contributors" / "emails"
    emails.mkdir(parents=True)
    tsv = tmp_path / "contributors" / "case-collisions.tsv"
    monkeypatch.setattr(add_contributor, "EMAILS_DIR", emails)
    monkeypatch.setattr(add_contributor, "COLLISIONS_FILE", tsv)

    assert add_contributor.add_contributor("dev@Box.local", "first") == 0
    assert (emails / "dev@Box.local").is_file()

    # Same email bar case, different person -> both move to the TSV so the
    # working tree never needs two filenames differing only in case.
    assert add_contributor.add_contributor("dev@box.local", "second") == 0
    assert not (emails / "dev@Box.local").exists()
    assert add_contributor.read_collisions() == {
        "dev@Box.local": "first",
        "dev@box.local": "second",
    }


def test_add_contributor_is_idempotent_for_collision_entries(tmp_path, monkeypatch):
    import add_contributor

    emails = tmp_path / "contributors" / "emails"
    emails.mkdir(parents=True)
    tsv = tmp_path / "contributors" / "case-collisions.tsv"
    tsv.write_text("dev@Box.local\tfirst\n", encoding="utf-8")
    monkeypatch.setattr(add_contributor, "EMAILS_DIR", emails)
    monkeypatch.setattr(add_contributor, "COLLISIONS_FILE", tsv)

    assert add_contributor.add_contributor("dev@Box.local", "first") == 0
    assert add_contributor.add_contributor("dev@Box.local", "other") == 1
    assert tsv.read_text(encoding="utf-8") == "dev@Box.local\tfirst\n"


def test_audit_is_mapped_accepts_collision_rows():
    sys.path.insert(0, str(SCRIPTS_DIR))
    import audit_pr_attribution

    for email in release._load_contributor_collisions():
        assert audit_pr_attribution.is_mapped(email)
