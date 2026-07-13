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


def test_active_brand_identity_derives_loop24(monkeypatch):
    def fake_resolve_active_brand(*a, **k):
        return "loop24"

    def fake_load_brand(slug, *a, **k):
        assert slug == "loop24"
        return {"displayName": "LOOP24", "scheme": "loop24"}

    monkeypatch.setattr(
        "hermes_cli.brand_config.resolve_active_brand", fake_resolve_active_brand
    )
    monkeypatch.setattr("hermes_cli.brand_config.load_brand", fake_load_brand)

    assert u._active_brand_identity() == ("LOOP24", ["loop24", "hermes"])


def test_active_brand_identity_derives_otto(monkeypatch):
    def fake_resolve_active_brand(*a, **k):
        return "otto"

    def fake_load_brand(slug, *a, **k):
        assert slug == "otto"
        return {"displayName": "OTTO", "scheme": "otto"}

    monkeypatch.setattr(
        "hermes_cli.brand_config.resolve_active_brand", fake_resolve_active_brand
    )
    monkeypatch.setattr("hermes_cli.brand_config.load_brand", fake_load_brand)

    assert u._active_brand_identity() == ("OTTO", ["otto", "hermes"])


def test_active_brand_identity_falls_back_to_otto_on_failure(monkeypatch):
    def raise_resolve(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "hermes_cli.brand_config.resolve_active_brand", raise_resolve
    )

    assert u._active_brand_identity() == ("OTTO", ["otto", "hermes"])


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


def test_remove_protocols_accepts_custom_schemes(monkeypatch):
    # A LOOP24 install must clean loop24:// (+ hermes://), not otto://.
    deleted = []

    class FakeWinreg:
        HKEY_CURRENT_USER = 0

        @staticmethod
        def DeleteKey(root, sub):
            deleted.append(sub)

    monkeypatch.setitem(sys.modules, "winreg", FakeWinreg)

    out = u.remove_deep_link_protocols_windows(["loop24", "hermes"])

    assert "loop24" in out and "hermes" in out
    assert "otto" not in out
    assert any(sub.startswith("Software\\Classes\\loop24") for sub in deleted)
