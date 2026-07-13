from pathlib import Path
import sys
import hermes_cli.uninstall as u


def test_bin_is_a_path_marker():
    markers = u._hermes_path_markers(Path(r"C:\Users\x\AppData\Local\hermes"))
    assert any(m.endswith(r"\bin") for m in markers), markers


def test_interpreter_inside_detects_venv_under_home(tmp_path):
    home = tmp_path / "hermes"
    (home / "hermes-agent" / "venv" / "Scripts").mkdir(parents=True)
    exe = home / "hermes-agent" / "venv" / "Scripts" / "python.exe"
    exe.write_text("")
    assert u._interpreter_inside(home, executable=str(exe)) is True
    assert u._interpreter_inside(home, executable=r"C:\Python311\python.exe") is False


def test_remove_desktop_shortcuts_unlinks_only_matching(tmp_path, monkeypatch):
    # Two fake shortcut dirs; only the OTTO.lnk should be removed.
    sm = tmp_path / "startmenu"; dk = tmp_path / "desktop"
    sm.mkdir(); dk.mkdir()
    (sm / "OTTO.lnk").write_text(""); (sm / "Other.lnk").write_text("")
    (dk / "OTTO.lnk").write_text("")
    removed = u.remove_desktop_shortcuts("OTTO", dirs=[sm, dk])
    names = sorted(p.name for p in removed)
    assert names == ["OTTO.lnk", "OTTO.lnk"]
    assert (sm / "Other.lnk").exists()
