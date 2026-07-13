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


def test_remove_protocols_missing_intermediate_still_deletes_root(monkeypatch):
    # Regression test: remove_deep_link_protocols_windows() must delete each
    # of the 4 registry keys independently. Previously all 4 DeleteKey calls
    # shared one try/except FileNotFoundError, so a missing intermediate key
    # (e.g. the deepest "...\\shell\\open\\command" already gone) aborted the
    # whole chain and left the root "Software\\Classes\\<scheme>" key behind.
    deleted = []

    class FakeWinreg:
        HKEY_CURRENT_USER = 0

        @staticmethod
        def DeleteKey(root, sub):
            # Simulate: the deepest 'command' key is already gone, but the
            # shallower keys (including the root) still exist.
            if sub.endswith("command"):
                raise FileNotFoundError
            deleted.append(sub)

    monkeypatch.setitem(sys.modules, "winreg", FakeWinreg)

    out = u.remove_deep_link_protocols_windows()

    # Root keys for both schemes must have been deleted despite the missing
    # 'command' key raising FileNotFoundError first in the chain.
    assert "Software\\Classes\\otto" in deleted
    assert "Software\\Classes\\hermes" in deleted
    assert "otto" in out and "hermes" in out
