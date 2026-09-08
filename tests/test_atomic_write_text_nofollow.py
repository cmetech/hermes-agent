"""Security contract for opt-in no-follow atomic text replacement."""

from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest

import utils
from utils import atomic_write_text


def test_default_atomic_writer_still_follows_the_existing_symlink_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.txt"
    target.write_text("old\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    atomic_write_text(link, "new\n")

    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == "new\n"


def test_no_follow_writer_rejects_final_and_intermediate_symlinks(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "state.json"
    target.write_text("old\n", encoding="utf-8")
    link = tmp_path / "state.json"
    link.symlink_to(target)

    with pytest.raises(OSError):
        atomic_write_text(link, "new\n", no_follow=True)
    assert target.read_text(encoding="utf-8") == "old\n"

    parent_link = tmp_path / "linked-parent"
    parent_link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        atomic_write_text(parent_link / "new.json", "new\n", no_follow=True)
    assert not (outside / "new.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory descriptors")
def test_no_follow_writer_anchors_replace_when_parent_path_is_swapped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "state"
    parent.mkdir()
    target = parent / "state.json"
    target.write_text("old\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    detached = tmp_path / "detached"
    real_replace = utils.os.replace
    swapped = False

    def swap_then_replace(source, destination, *args, **kwargs):
        nonlocal swapped
        if not swapped and kwargs.get("dst_dir_fd") is not None:
            swapped = True
            parent.rename(detached)
            parent.symlink_to(outside, target_is_directory=True)
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(utils.os, "replace", swap_then_replace)

    with pytest.raises(OSError):
        atomic_write_text(target, "new\n", no_follow=True)

    assert swapped is True
    assert not (outside / "state.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_no_follow_writer_refuses_existing_read_only_file(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text("old\n", encoding="utf-8")
    target.chmod(0o400)

    with pytest.raises(PermissionError):
        atomic_write_text(target, "new\n", no_follow=True)

    assert target.read_text(encoding="utf-8") == "old\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o400


def test_no_follow_writer_rejects_windows_reparse_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "state.json"
    parent_identity = (tmp_path.stat().st_dev, tmp_path.stat().st_ino)
    original = utils._is_reparse_point

    def mark_parent_reparse(metadata) -> bool:
        identity = (metadata.st_dev, metadata.st_ino)
        return identity == parent_identity or original(metadata)

    monkeypatch.setattr(utils, "_is_reparse_point", mark_parent_reparse)

    with pytest.raises(OSError):
        atomic_write_text(target, "new\n", no_follow=True)

    assert not target.exists()
