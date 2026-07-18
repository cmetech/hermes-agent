import pytest

import hermes_constants


@pytest.mark.parametrize("basename", ["hermes", ".hermes"])
def test_version_agent_label_preserves_neutral_identity(monkeypatch, basename):
    monkeypatch.setattr(hermes_constants, "home_dir_basename", lambda: basename)

    assert hermes_constants.version_agent_label() == "Hermes Agent"


@pytest.mark.parametrize("basename", ["otto", ".otto", "loop24", ".loop24"])
def test_version_agent_label_uses_generic_branded_identity(monkeypatch, basename):
    monkeypatch.setattr(hermes_constants, "home_dir_basename", lambda: basename)

    assert hermes_constants.version_agent_label() == "Co-worker Agent"
