import importlib
from hermes_cli import config as cfg

def test_capability_keys_registered_from_vendored_manifest():
    # The vendored manifest (capabilities/ericsson.json) drives registration.
    ov = cfg.OPTIONAL_ENV_VARS
    for key in ("JIRA_BASE_URL", "JIRA_PAT", "GLEAN_MCP_URL", "GLEAN_API_TOKEN"):
        assert key in ov, f"{key} should be registered from the vendored capability manifest"
        assert ov[key]["category"] == "tool"
    assert ov["JIRA_PAT"]["password"] is True
    assert ov["GLEAN_API_TOKEN"]["password"] is True
    # ERICSSON_ENV (category 'skill' in the manifest) must NOT be registered as a tool key
    assert cfg.OPTIONAL_ENV_VARS.get("ERICSSON_ENV", {}).get("category") != "tool"
