from hermes_cli import config as cfg


def test_capability_keys_registered_from_vendored_manifest():
    # The vendored manifest (capabilities/ericsson.json) drives registration.
    ov = cfg.OPTIONAL_ENV_VARS
    for key in ("GLEAN_API_TOKEN", "ERICSSON_GRAPH_CLIENT_ID"):
        assert key in ov, f"{key} should be registered from the vendored capability manifest"
        assert ov[key]["category"] == "tool"
    assert "JIRA_BASE_URL" not in ov
    assert "JIRA_PAT" not in ov
    assert "GLEAN_MCP_URL" not in ov
    assert ov["GLEAN_API_TOKEN"]["password"] is True
    assert ov["ERICSSON_GRAPH_CLIENT_ID"].get("password") is not True
    assert "ERICSSON_ENV" not in ov
